"""Daily audit triplet writer.

Per Phase 5 brief: every market day produces three artifacts under
`state/audit/<date>/`:

  - events.jsonl       — append-only event log (anchor_captured,
                         levels_computed, oco_pair_placed, order_filled,
                         peer_cancelled, sl_modified, position_closed,
                         guardrail_blocked, square_off, heartbeat).
                         One JSON object per line; `ts_ist` and `symbol`
                         on every event.
  - daily_summary.json — end-of-day metrics: per-symbol trades,
                         gross_pnl, net_pnl, win/loss, max_drawdown,
                         anchor, square_off_time.
  - state_snapshot.json — final StateStore snapshot.

Append-only means events.jsonl tolerates a mid-session crash without
losing prior events; the summary and snapshot are written via the atomic
StateStore (tmp + os.replace) at end-of-day or on graceful shutdown.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.logger import get_logger
from core.state_store import StateStore
from core.time_utils import now_ist

logger = get_logger("audit")

AUDIT_ROOT = Path("state/audit")


@dataclass
class TradeSnapshot:
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    points_pnl: float
    money_pnl: float
    exit_reason: str


@dataclass
class DailyMetrics:
    date_iso: str
    anchors: dict[str, float] = field(default_factory=dict)
    trades: list[TradeSnapshot] = field(default_factory=list)
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    max_drawdown: float = 0.0
    square_off_time: Optional[str] = None
    mode: str = "PAPER"


class DailyAudit:
    def __init__(self, date_iso: str, root: Path = AUDIT_ROOT) -> None:
        self.date_iso = date_iso
        self.dir = root / date_iso
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.jsonl"
        self.summary_path = self.dir / "daily_summary.json"
        self.snapshot_path = self.dir / "state_snapshot.json"
        self.metrics = DailyMetrics(date_iso=date_iso)
        self._running_low_pnl = 0.0  # for drawdown calc

    # ---- events.jsonl (append-only) ----

    def emit(self, event_type: str, symbol: Optional[str], payload: dict[str, Any]) -> None:
        line = json.dumps({
            "ts_ist": now_ist().isoformat(),
            "type": event_type,
            "symbol": symbol,
            "payload": payload,
        })
        # Open per-event so a crash mid-session does not lose the prior
        # events. Cost is acceptable: well under tick rate.
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ---- metrics accumulation ----

    def record_trade(self, trade: TradeSnapshot) -> None:
        self.metrics.trades.append(trade)
        self.metrics.gross_pnl = round(self.metrics.gross_pnl + trade.money_pnl, 2)
        if trade.points_pnl > 0:
            self.metrics.wins += 1
        else:
            self.metrics.losses += 1
        # Cumulative net PnL (gross until Phase 6 cost model lands).
        self.metrics.net_pnl = self.metrics.gross_pnl
        running = self.metrics.net_pnl
        if running < self._running_low_pnl:
            self._running_low_pnl = running
        peak = max(self.metrics.net_pnl, 0.0)
        drawdown = peak - self._running_low_pnl if self._running_low_pnl < 0 else 0.0
        if drawdown > self.metrics.max_drawdown:
            self.metrics.max_drawdown = round(drawdown, 2)

    def set_anchor(self, symbol: str, anchor_price: float) -> None:
        self.metrics.anchors[symbol] = anchor_price

    def set_square_off_time(self, iso: str) -> None:
        self.metrics.square_off_time = iso

    def set_mode(self, mode: str) -> None:
        self.metrics.mode = mode

    # ---- triplet flush ----

    def flush(self, state_store: Optional[StateStore] = None) -> None:
        """Atomically write summary + snapshot. Call on graceful shutdown
        and at the end of the trading day.
        """
        summary = {
            "date": self.metrics.date_iso,
            "mode": self.metrics.mode,
            "anchors": self.metrics.anchors,
            "square_off_time": self.metrics.square_off_time,
            "trade_count": len(self.metrics.trades),
            "wins": self.metrics.wins,
            "losses": self.metrics.losses,
            "gross_pnl": self.metrics.gross_pnl,
            "net_pnl": self.metrics.net_pnl,
            "max_drawdown": self.metrics.max_drawdown,
            "trades": [t.__dict__ for t in self.metrics.trades],
        }
        _atomic_write_json(self.summary_path, summary)

        if state_store is not None:
            _atomic_write_json(self.snapshot_path, state_store.load())


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
