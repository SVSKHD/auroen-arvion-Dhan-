"""Anchor capture for the anchor-breakout strategy.

The anchor is the reference price at the configured anchor time (e.g. 09:15
IST = NSE open M5 bar). The strategy builds long/short trigger levels off
this price for the whole session. Each symbol gets one anchor per IST trading
day; the anchor must survive a mid-session restart.

Persisted shape (via StateStore):

    {
      "anchors": {
        "2026-01-05": {
          "NIFTY":     {"anchor_price": 19800.5,
                        "captured_at": "2026-01-05T09:15:03+05:30",
                        "source_bar_time": "2026-01-05T09:15:00+05:30"},
          "BANKNIFTY": {...}
        },
        "2026-01-04": { ... }   # stale; purged when the IST date rolls
      }
    }

`should_capture_anchor(symbol)` is the gate the live/paper loops poll. It
returns True only if all of:
  - clock is inside [anchor_time, anchor_time + capture_window]
  - this symbol's anchor for today is not already stored
  - we are inside trading hours
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

from core.logger import get_logger
from core.state_store import StateStore
from core.time_utils import IST, NSE_CLOSE, NSE_OPEN, in_trading_hours, now_ist

logger = get_logger("anchor_engine")

ANCHORS_KEY = "anchors"


@dataclass
class AnchorRecord:
    symbol: str
    anchor_price: float
    captured_at: datetime
    source_bar_time: datetime

    def to_dict(self) -> dict:
        return {
            "anchor_price": self.anchor_price,
            "captured_at": self.captured_at.isoformat(),
            "source_bar_time": self.source_bar_time.isoformat(),
        }

    @classmethod
    def from_dict(cls, symbol: str, payload: dict) -> "AnchorRecord":
        return cls(
            symbol=symbol,
            anchor_price=float(payload["anchor_price"]),
            captured_at=datetime.fromisoformat(payload["captured_at"]),
            source_bar_time=datetime.fromisoformat(payload["source_bar_time"]),
        )


class AnchorEngine:
    def __init__(
        self,
        state_store: StateStore,
        anchor_time: time = NSE_OPEN,
        capture_window_seconds: int = 30,
        trading_open: time = NSE_OPEN,
        trading_close: time = NSE_CLOSE,
    ) -> None:
        self.state_store = state_store
        self.anchor_time = anchor_time
        self.capture_window_seconds = capture_window_seconds
        self.trading_open = trading_open
        self.trading_close = trading_close

        # In-memory cache of today's anchors per symbol. Hydrated lazily on
        # the first method call so injected `now` arguments in tests control
        # which IST date the engine binds to, not the real wall-clock at
        # construction time.
        self._anchors_today: dict[str, AnchorRecord] = {}
        self._current_day_iso: Optional[str] = None

    def should_capture_anchor(self, symbol: str, now: Optional[datetime] = None) -> bool:
        now = now or now_ist()
        self._roll_day_if_needed(now)

        if not in_trading_hours(now, self.trading_open, self.trading_close):
            return False

        if symbol in self._anchors_today:
            return False

        return self._inside_capture_window(now)

    def capture_anchor(
        self,
        symbol: str,
        anchor_price: float,
        source_bar_time: datetime,
        now: Optional[datetime] = None,
    ) -> AnchorRecord:
        now = now or now_ist()
        self._roll_day_if_needed(now)

        record = AnchorRecord(
            symbol=symbol,
            anchor_price=anchor_price,
            captured_at=now,
            source_bar_time=source_bar_time,
        )
        self._anchors_today[symbol] = record
        self._persist()

        logger.info(
            "ANCHOR | day=%s symbol=%s price=%.2f source_bar=%s captured_at=%s",
            self._current_day_iso,
            symbol,
            anchor_price,
            source_bar_time.isoformat(),
            now.isoformat(),
        )
        return record

    def get_anchor(self, symbol: str, now: Optional[datetime] = None) -> Optional[AnchorRecord]:
        now = now or now_ist()
        self._roll_day_if_needed(now)
        return self._anchors_today.get(symbol)

    def all_anchors_today(self, now: Optional[datetime] = None) -> dict[str, AnchorRecord]:
        now = now or now_ist()
        self._roll_day_if_needed(now)
        return dict(self._anchors_today)

    def _inside_capture_window(self, now: datetime) -> bool:
        anchor_dt = now.replace(
            hour=self.anchor_time.hour,
            minute=self.anchor_time.minute,
            second=0,
            microsecond=0,
        )
        window_end = anchor_dt + timedelta(seconds=self.capture_window_seconds)
        return anchor_dt <= now <= window_end

    def _roll_day_if_needed(self, now: datetime) -> None:
        today_iso = now.date().isoformat()
        if today_iso == self._current_day_iso:
            return
        self._reload(now)

    def _reload(self, now: datetime) -> None:
        today_iso = now.date().isoformat()
        self._current_day_iso = today_iso
        self._anchors_today = {}

        payload = self.state_store.load()
        anchors_by_date = payload.get(ANCHORS_KEY, {}) or {}

        for symbol, raw in (anchors_by_date.get(today_iso, {}) or {}).items():
            try:
                self._anchors_today[symbol] = AnchorRecord.from_dict(symbol, raw)
            except (KeyError, ValueError) as exc:
                logger.warning(
                    "Discarding malformed anchor record day=%s symbol=%s err=%s",
                    today_iso,
                    symbol,
                    exc,
                )

        # Purge any non-today entries by writing back only today's anchors.
        if set(anchors_by_date.keys()) - {today_iso}:
            logger.info(
                "Purging stale anchor dates: %s",
                sorted(set(anchors_by_date.keys()) - {today_iso}),
            )
            self._persist()

    def _persist(self) -> None:
        payload = self.state_store.load()
        anchors = payload.get(ANCHORS_KEY, {}) or {}
        anchors = {self._current_day_iso: {
            sym: rec.to_dict() for sym, rec in self._anchors_today.items()
        }}
        payload[ANCHORS_KEY] = anchors
        self.state_store.save(payload)


__all__ = ["AnchorEngine", "AnchorRecord", "IST"]
