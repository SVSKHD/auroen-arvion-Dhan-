"""Dhan v2 intraday historical OHLC fetcher with a CSV-on-disk cache.

Endpoint shape verified against the official DhanHQ-py SDK
(`_historical_data.py:intraday_minute_data`):

    POST /v2/charts/intraday
    {
      "securityId":      "<id>",
      "exchangeSegment": "NSE_EQ" | "NSE_FNO" | "MCX_COMM" | ...,
      "instrument":      "FUTIDX" | "OPTIDX" | "FUTCOM" | ...,
      "interval":        "1" | "5" | "15" | "25" | "60",
      "oi":              false,
      "fromDate":        "YYYY-MM-DD",
      "toDate":          "YYYY-MM-DD"
    }

Response: `{ "open": [...], "high": [...], "low": [...], "close": [...],
"volume": [...], "timestamp": [epoch_seconds, ...] }`.

The cache key is `<security_id>_<from>_<to>_<interval>m.csv`, written
under `data/cache/`. A cache hit skips the HTTP call entirely. This lets
the backtest re-run cheaply during parameter sweeps.
"""

from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from brokers.dhan.dhan_client import DhanClient
from core.logger import get_logger

logger = get_logger("dhan_historical")

CACHE_ROOT = Path("data/cache")


class DhanHistoricalData:
    def __init__(self, client: Optional[DhanClient] = None, cache_root: Path = CACHE_ROOT) -> None:
        self.client = client or DhanClient()
        self.cache_root = cache_root

    def fetch_intraday(
        self,
        security_id: str,
        exchange_segment: str,
        from_date: date,
        to_date: date,
        interval: int = 5,
        instrument: str = "FUTIDX",
    ) -> pd.DataFrame:
        cache_path = self._cache_path(security_id, from_date, to_date, interval)
        if cache_path.exists():
            logger.info("CACHE HIT %s", cache_path)
            return pd.read_csv(cache_path, parse_dates=["time"])

        payload = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment.upper(),
            "instrument": instrument.upper(),
            "interval": str(interval),
            "oi": False,
            "fromDate": from_date.isoformat(),
            "toDate": to_date.isoformat(),
        }
        logger.info("FETCH /charts/intraday %s %s..%s @ %dm", security_id, from_date, to_date, interval)
        raw = self.client.post("/charts/intraday", payload)
        df = self._raw_to_df(raw)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)
        return df

    def _cache_path(self, security_id: str, from_date: date, to_date: date, interval: int) -> Path:
        name = f"{security_id}_{from_date.isoformat()}_{to_date.isoformat()}_{interval}m.csv"
        return self.cache_root / name

    @staticmethod
    def _raw_to_df(raw: dict) -> pd.DataFrame:
        if not isinstance(raw, dict):
            raise ValueError(f"Unexpected /charts/intraday response: {type(raw).__name__}")
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
