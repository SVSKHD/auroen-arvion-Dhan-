"""HistoricalDataFetcher — cache write/read, 429 backoff, DataFrame shape.

We mock the underlying `DhanClient` so no Telegram/Dhan calls leave the
box.
"""

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import requests

from brokers.dhan.historical_data import (
    HistoricalDataFetcher,
    HistoricalFetchError,
)


class _MockClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, path: str, payload: dict) -> Any:
        self.calls.append({"path": path, "payload": dict(payload)})
        if not self.responses:
            return {"open": [], "high": [], "low": [], "close": [],
                    "volume": [], "timestamp": []}
        entry = self.responses.pop(0)
        if isinstance(entry, Exception):
            raise entry
        return entry


def _ohlc_response(start_epoch: int = 1736046900) -> dict:
    return {
        "open":   [100.0, 101.0, 102.0],
        "high":   [100.5, 101.5, 102.5],
        "low":    [99.5,  100.5, 101.5],
        "close":  [100.5, 101.5, 102.5],
        "volume": [1000,  1200,  900],
        "timestamp": [start_epoch, start_epoch + 300, start_epoch + 600],
    }


# --------------------------------------------------------------- fetch + cache


def test_first_fetch_writes_cache_and_returns_df(tmp_path):
    fetcher = HistoricalDataFetcher(
        client=_MockClient([_ohlc_response()]),
        cache_root=tmp_path / "cache",
        sleep_fn=lambda _s: None,
    )
    df = fetcher.fetch_intraday(
        security_id="49081",
        exchange_segment="NSE_FNO",
        from_date=date(2025, 1, 5),
        to_date=date(2025, 1, 5),
        resolution="5",
    )
    assert set(df.columns) == {"time", "open", "high", "low", "close", "volume"}
    assert len(df) == 3

    # Cache file written under the expected path.
    expected = tmp_path / "cache" / "49081_2025-01-05_2025-01-05_5m.csv"
    assert expected.exists()


def test_cache_hit_skips_http_call(tmp_path):
    """Second call with the same params loads from disk and doesn't
    touch the HTTP client at all."""
    client = _MockClient([_ohlc_response()])
    fetcher = HistoricalDataFetcher(
        client=client,
        cache_root=tmp_path / "cache",
        sleep_fn=lambda _s: None,
    )
    # Prime the cache.
    fetcher.fetch_intraday("49081", "NSE_FNO", date(2025, 1, 5), date(2025, 1, 5), "5")
    assert len(client.calls) == 1

    # Second fetch should not call again.
    df2 = fetcher.fetch_intraday("49081", "NSE_FNO", date(2025, 1, 5), date(2025, 1, 5), "5")
    assert len(client.calls) == 1
    assert len(df2) == 3


def test_cache_key_includes_resolution(tmp_path):
    """1m and 5m must not collide in the cache."""
    client = _MockClient([_ohlc_response(), _ohlc_response(start_epoch=1736046800)])
    fetcher = HistoricalDataFetcher(
        client=client,
        cache_root=tmp_path / "cache",
        sleep_fn=lambda _s: None,
    )
    fetcher.fetch_intraday("49081", "NSE_FNO", date(2025, 1, 5), date(2025, 1, 5), "5")
    fetcher.fetch_intraday("49081", "NSE_FNO", date(2025, 1, 5), date(2025, 1, 5), "1")
    assert len(client.calls) == 2  # both fetched, neither cached the other


# --------------------------------------------------------------- backoff


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    err = requests.HTTPError(f"{status} error")
    err.response = response
    return err


def test_429_retries_with_backoff_then_succeeds(tmp_path):
    """429 first, then 200. The fetcher must back off and retry."""
    sleeps: list[float] = []

    client = _MockClient([
        _http_error(429),
        _http_error(429),
        _ohlc_response(),
    ])
    fetcher = HistoricalDataFetcher(
        client=client,
        cache_root=tmp_path / "cache",
        sleep_fn=sleeps.append,
    )
    df = fetcher.fetch_intraday("49081", "NSE_FNO", date(2025, 1, 5), date(2025, 1, 5))
    assert len(df) == 3
    assert sleeps == [1.0, 2.0]  # first two backoffs from RETRY_BACKOFFS


def test_429_raises_after_max_attempts(tmp_path):
    """Persistent 429 (more than RETRY_BACKOFFS attempts) raises."""
    client = _MockClient([_http_error(429)] * 10)  # always 429
    fetcher = HistoricalDataFetcher(
        client=client,
        cache_root=tmp_path / "cache",
        sleep_fn=lambda _s: None,
    )
    with pytest.raises(requests.HTTPError):
        fetcher.fetch_intraday("49081", "NSE_FNO", date(2025, 1, 5), date(2025, 1, 5))


def test_4xx_other_than_429_raises_immediately(tmp_path):
    """A 400 means bad payload — no point retrying."""
    client = _MockClient([_http_error(400)])
    fetcher = HistoricalDataFetcher(
        client=client,
        cache_root=tmp_path / "cache",
        sleep_fn=lambda _s: None,
    )
    with pytest.raises(requests.HTTPError):
        fetcher.fetch_intraday("49081", "NSE_FNO", date(2025, 1, 5), date(2025, 1, 5))


# --------------------------------------------------------------- payload shape


def test_payload_contains_dhan_v2_required_keys(tmp_path):
    client = _MockClient([_ohlc_response()])
    fetcher = HistoricalDataFetcher(
        client=client,
        cache_root=tmp_path / "cache",
        sleep_fn=lambda _s: None,
    )
    fetcher.fetch_intraday(
        security_id="49081", exchange_segment="NSE_FNO",
        from_date=date(2025, 1, 5), to_date=date(2025, 1, 5),
        resolution="5", instrument="FUTIDX",
    )
    payload = client.calls[0]["payload"]
    assert payload["securityId"] == "49081"
    assert payload["exchangeSegment"] == "NSE_FNO"
    assert payload["instrument"] == "FUTIDX"
    assert payload["interval"] == "5"
    assert payload["oi"] is False
    assert payload["fromDate"] == "2025-01-05"
    assert payload["toDate"] == "2025-01-05"
