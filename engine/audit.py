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
                         equity_curve, anchor, square_off_time.
  - state_snapshot.json — final StateStore snapshot.

Pass A: net_pnl is gross minus India intraday costs (via
IndiaIntradayCostModel injected on construction). If the cost model is
None, net_pnl == gross_pnl and the caller is warned at construction
time — a stale net_pnl number is more dangerous than an explicit
"costs not modelled" signal.

Drawdown + equity-curve bookkeeping shared with `BacktestEngine`
through `engine.metrics.DrawdownTracker`.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.logger import get_logger
from core.state_store import StateStore
from core.time_utils import now_ist
from engine.metrics import DrawdownTracker

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
    quantity: int = 0
    segment: str = ""
    instrument_class: str = ""
    costs: Optional[dict] = None  # TradeCosts.as_dict() when cost model attached
    net_money_pnl: float = 0.0


@dataclass
class DailyMetrics:
    date_iso: str
    anchors: dict[str, float] = field(default_factory=dict)
    trades: list[TradeSnapshot] = field(default_factory=list)
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    total_costs: float = 0.0
    wins: int = 0
    losses: int = 0
    max_drawdown_money: float = 0.0
    max_drawdown_pct: float = 0.0
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    square_off_time: Optional[str] = None
    mode: str = "PAPER"


class DailyAudit:
    def __init__(
        self,
        date_iso: str,
        root: Path = AUDIT_ROOT,
        cost_model=None,
    ) -> None:
        """`cost_model` is an `IndiaIntradayCostModel` (or test double with the
        same `round_trip_cost` signature). When omitted, net_pnl == gross_pnl
        and a one-shot warning is logged.
        """
        self.date_iso = date_iso
        self.dir = root / date_iso
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.jsonl"
        self.summary_path = self.dir / "daily_summary.json"
        self.snapshot_path = self.dir / "state_snapshot.json"
        self.metrics = DailyMetrics(date_iso=date_iso)
        self.cost_model = cost_model
        self._drawdown = DrawdownTracker()
        if cost_model is None:
            logger.warning(
                "DailyAudit constructed without a cost_model: net_pnl will "
                "equal gross_pnl. Pass an IndiaIntradayCostModel for "
                "production accounting."
            )

    # ---- events.jsonl (append-only) ----

    def emit(self, event_type: str, symbol: Optional[str], payload: dict[str, Any]) -> None:
        line = json.dumps({
            "ts_ist": now_ist().isoformat(),
            "type": event_type,
            "symbol": symbol,
            "payload": payload,
        })
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ---- metrics accumulation ----

    def record_trade(
        self,
        trade: TradeSnapshot,
        exit_ts_iso: Optional[str] = None,
    ) -> None:
        # Apply cost model if attached. We compute costs at record time so
        # callers don't have to know the model exists.
        if self.cost_model is not None and trade.quantity > 0:
            tc = self.cost_model.round_trip_cost(
                segment=trade.segment,
                side=trade.side,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                quantity=trade.quantity,
                instrument_class=trade.instrument_class or None,
            )
            trade.costs = tc.as_dict()
            trade.net_money_pnl = round(trade.money_pnl - tc.total, 2)
            self.metrics.total_costs = round(self.metrics.total_costs + tc.total, 2)
        else:
            trade.costs = None
            trade.net_money_pnl = trade.money_pnl

        self.metrics.trades.append(trade)
        self.metrics.gross_pnl = round(self.metrics.gross_pnl + trade.money_pnl, 2)
        self.metrics.net_pnl = round(self.metrics.gross_pnl - self.metrics.total_costs, 2)
        if trade.points_pnl > 0:
            self.metrics.wins += 1
        else:
            self.metrics.losses += 1

        # Shared drawdown/equity-curve bookkeeping with BacktestEngine.
        ts = exit_ts_iso or now_ist().isoformat()
        self._drawdown.record(trade.net_money_pnl, ts)
        self.metrics.max_drawdown_money = self._drawdown.max_drawdown_money
        self.metrics.max_drawdown_pct = self._drawdown.max_drawdown_pct
        self.metrics.equity_curve = list(self._drawdown.equity_curve)

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
            "total_costs": self.metrics.total_costs,
            "max_drawdown_money": self.metrics.max_drawdown_money,
            "max_drawdown_pct": self.metrics.max_drawdown_pct,
            "equity_curve": self.metrics.equity_curve,
            "cost_model_attached": self.cost_model is not None,
            "trades": [t.__dict__ for t in self.metrics.trades],
        }
        _atomic_write_json(self.summary_path, summary)

        if state_store is not None:
            _atomic_write_json(self.snapshot_path, state_store.load())


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
