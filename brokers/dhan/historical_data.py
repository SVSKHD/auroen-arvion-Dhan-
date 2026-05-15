"""Dhan v2 historical intraday OHLC fetcher with a CSV-on-disk cache.

Verified against the official DhanHQ-py SDK
(dhan-oss/DhanHQ-py:_historical_data.py:intraday_minute_data):

    POST /v2/charts/intraday
    {
      "securityId": "<id>",
      "exchangeSegment": "NSE_EQ" | "NSE_FNO" | "MCX_COMM" | ...,
      "instrument": "FUTIDX" | "OPTIDX" | "FUTCOM" | ...,
      "interval": "1" | "5" | "15" | "25" | "60",
      "oi": false,
      "fromDate": "YYYY-MM-DD",
      "toDate": "YYYY-MM-DD"
    }

Response shape: `{"open":[...], "high":[...], "low":[...], "close":[...],
"volume":[...], "timestamp":[<epoch_seconds>, ...]}` (or wrapped in a
`{"data": ...}` envelope; we handle both).

Cache: `data/cache/<security_id>_<from>_<to>_<resolution>m.csv`. Settled
historical bars are immutable, so the cache is never invalidated. Cache
hits skip the API call entirely — this is what lets parameter sweeps
re-run cheaply.

Rate limits: Dhan returns HTTP 429 when over the per-second cap. We back
off exponentially (1s, 2s, 4s, 8s) and retry up to 4 times before
raising. 5xx is retried on the same schedule. 4xx (other than 429)
surfaces immediately.
"""

import time as time_module
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests

from brokers.dhan.dhan_client import DhanClient
from core.logger import get_logger

logger = get_logger("dhan_historical")

CACHE_ROOT = Path("data/cache")
RETRY_BACKOFFS = (1.0, 2.0, 4.0, 8.0)


class HistoricalFetchError(Exception):
    pass


class HistoricalDataFetcher:
    """Fetches intraday OHLC bars from Dhan v2 with a write-through CSV
    cache. The cache key includes the resolution so 1m and 5m sweeps
    don't collide.
    """

    def __init__(
        self,
        client: Optional[DhanClient] = None,
        cache_root: Path = CACHE_ROOT,
        sleep_fn=time_module.sleep,
    ) -> None:
        self.client = client or DhanClient()
        self.cache_root = cache_root
        self._sleep = sleep_fn

    def fetch_intraday(
        self,
        security_id: str,
        exchange_segment: str,
        from_date: date,
        to_date: date,
        resolution: str = "5",
        instrument: str = "FUTIDX",
    ) -> pd.DataFrame:
        cache_path = self._cache_path(security_id, from_date, to_date, resolution)
        if cache_path.exists():
            logger.info(
                "historical_cache_hit",
                extra={"path": str(cache_path)},
            )
            return pd.read_csv(cache_path, parse_dates=["time"])

        payload = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment.upper(),
            "instrument": instrument.upper(),
            "interval": str(resolution),
            "oi": False,
            "fromDate": from_date.isoformat(),
            "toDate": to_date.isoformat(),
        }
        logger.info(
            "historical_fetch",
            extra={
                "security_id": security_id,
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "resolution": resolution,
            },
        )
        raw = self._post_with_backoff("/charts/intraday", payload)
        df = self._raw_to_df(raw)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)
        return df

    # ------------------------------------------------------- internals

    def _cache_path(
        self, security_id: str, from_date: date, to_date: date, resolution: str
    ) -> Path:
        name = (
            f"{security_id}_{from_date.isoformat()}_"
            f"{to_date.isoformat()}_{resolution}m.csv"
        )
        return self.cache_root / name

    def _post_with_backoff(self, path: str, payload: dict[str, Any]) -> Any:
        """Wrap `DhanClient.post` so we can apply 429 backoff at this
        layer. `DhanClient` already retries 5xx + 429, so this is the
        belt-and-suspenders pass for the historical workload, which
        downloads in bulk and hits the rate limit harder than the live
        order path.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(len(RETRY_BACKOFFS) + 1):
            try:
                return self.client.post(path, payload)
            except requests.HTTPError as exc:
                last_exc = exc
                status = getattr(exc.response, "status_code", None)
                if status == 429 and attempt < len(RETRY_BACKOFFS):
                    delay = RETRY_BACKOFFS[attempt]
                    logger.warning(
                        "historical_rate_limited",
                        extra={"status": status, "delay_s": delay, "attempt": attempt + 1},
                    )
                    self._sleep(delay)
                    continue
                raise
            except Exception as exc:  # noqa: BLE001 — keep generic for client errors
                last_exc = exc
                if attempt < len(RETRY_BACKOFFS):
                    self._sleep(RETRY_BACKOFFS[attempt])
                    continue
                raise
        raise HistoricalFetchError(str(last_exc))

    @staticmethod
    def _raw_to_df(raw: Any) -> pd.DataFrame:
        if not isinstance(raw, dict):
            raise HistoricalFetchError(
                f"Unexpected /charts/intraday response: {type(raw).__name__}"
            )
        body = raw.get("data", raw) if "data" in raw else raw
        timestamps = body.get("timestamp") or body.get("start_Time") or []
        df = pd.DataFrame({
            "time": pd.to_datetime(timestamps, unit="s", utc=True).tz_convert("Asia/Kolkata"),
            "open": body.get("open", []),
            "high": body.get("high", []),
            "low": body.get("low", []),
            "close": body.get("close", []),
            "volume": body.get("volume", []),
        })
        return df
