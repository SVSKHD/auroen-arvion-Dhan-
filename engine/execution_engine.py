import os
from datetime import date, datetime
from typing import Literal, Optional

from brokers.base import BrokerAdapter
from core.logger import get_logger
from core.state_store import StateStore
from engine.strategy_runner import (
    StrategyConfig,
    StrategyPosition,
    StrategyRunner,
    StrategyTradeResult,
)
from risk.guardrails import GuardRails
from strategy.levels import OrderLevels

logger = get_logger("execution_engine")

Mode = Literal["PAPER", "LIVE"]


def _resolve_mode(explicit: Optional[str]) -> Mode:
    """Default PAPER. Refuse LIVE unless explicitly set to a known LIVE value."""
    raw = (explicit or os.getenv("MODE") or "PAPER").strip().upper()
    if raw == "LIVE":
        return "LIVE"
    if raw in {"", "PAPER", "DRYRUN", "DRY_RUN", "SIMULATION"}:
        return "PAPER"
    logger.warning("Ambiguous MODE=%r; defaulting to PAPER", raw)
    return "PAPER"


class ExecutionEngine:
    """Single bar/tick executor shared by PAPER and LIVE.

    Drives StrategyRunner per bar. State (open position per symbol, closed
    trades, running day pnl) is persisted atomically via StateStore on every
    change so a crash mid-session can recover cleanly.

    PAPER and LIVE produce identical strategy decisions; only side effects
    differ — LIVE places real broker orders, PAPER only records them.
    """

    def __init__(
        self,
        config: StrategyConfig,
        broker: Optional[BrokerAdapter] = None,
        guardrails: Optional[GuardRails] = None,
        state_store: Optional[StateStore] = None,
        mode: Optional[str] = None,
    ) -> None:
        self.mode: Mode = _resolve_mode(mode)
        if self.mode == "LIVE" and broker is None:
            raise RuntimeError("LIVE mode requires a broker adapter")

        self.config = config
        self.runner = StrategyRunner(config)
        self.broker = broker
        self.guardrails = guardrails
        self.state_store = state_store or StateStore("execution_state.json")

        self.positions: dict[str, StrategyPosition] = {}
        self.closed_trades: list[StrategyTradeResult] = []
        self.day_pnl: float = 0.0
        self.day_trades: int = 0
        self.current_day: Optional[date] = None
        self.rejections: list[dict] = []

    def on_bar(
        self,
        symbol: str,
        levels: OrderLevels,
        bar_time: datetime,
        high: float,
        low: float,
        close: float,
        quantity: int = 1,
    ) -> Optional[StrategyTradeResult]:
        self._roll_day_if_needed(bar_time.date())

        position = self.positions.get(symbol)

        if position is None:
            return self._maybe_enter(symbol, levels, bar_time, high, low, quantity)

        return self._evaluate_open(symbol, position, bar_time, high, low, close)

    def _maybe_enter(
        self,
        symbol: str,
        levels: OrderLevels,
        bar_time: datetime,
        high: float,
        low: float,
        quantity: int,
    ) -> None:
        if self.guardrails is not None:
            decision = self.guardrails.validate_trade_count(self.day_trades)
            if not decision.allowed:
                self._record_rejection(symbol, bar_time, decision.reason)
                return None
            decision = self.guardrails.validate_daily_loss(self.day_pnl)
            if not decision.allowed:
                self._record_rejection(symbol, bar_time, decision.reason)
                return None

        new_position = self.runner.detect_entry(
            symbol=symbol,
            levels=levels,
            bar_time=bar_time,
            high=high,
            low=low,
            quantity=quantity,
        )
        if new_position is None:
            return None

        self.positions[symbol] = new_position
        logger.info(
            "ENTRY | mode=%s symbol=%s side=%s entry=%.2f sl=%.2f tp=%.2f qty=%d",
            self.mode,
            symbol,
            new_position.side,
            new_position.entry_price,
            new_position.sl,
            new_position.tp,
            new_position.quantity,
        )
        # LIVE side-effects (real broker orders) are wired up in Phase 4/5.
        self._persist()
        return None

    def _evaluate_open(
        self,
        symbol: str,
        position: StrategyPosition,
        bar_time: datetime,
        high: float,
        low: float,
        close: float,
    ) -> Optional[StrategyTradeResult]:
        prev_sl = position.sl
        result = self.runner.evaluate_exit(
            position=position,
            bar_time=bar_time,
            high=high,
            low=low,
            close=close,
        )

        if result is not None:
            self.closed_trades.append(result)
            self.day_pnl += result.money_pnl
            self.day_trades += 1
            del self.positions[symbol]
            logger.info(
                "EXIT | mode=%s symbol=%s reason=%s exit=%.2f points=%.2f money=%.2f",
                self.mode,
                symbol,
                result.exit_reason,
                result.exit_price,
                result.points_pnl,
                result.money_pnl,
            )
            self._persist()
            return result

        if position.sl != prev_sl:
            logger.info(
                "TRAIL | symbol=%s lock_idx=%d new_sl=%.2f",
                symbol,
                position.lock_idx,
                position.sl,
            )
            self._persist()

        return None

    def _record_rejection(self, symbol: str, bar_time: datetime, reason: str) -> None:
        logger.warning(
            "ENTRY_REJECTED | symbol=%s time=%s reason=%s",
            symbol,
            bar_time.isoformat(),
            reason,
        )
        self.rejections.append(
            {"symbol": symbol, "time": bar_time.isoformat(), "reason": reason}
        )
        self._persist()

    def _roll_day_if_needed(self, today: date) -> None:
        if self.current_day == today:
            return
        if self.current_day is not None:
            logger.info(
                "Day roll %s -> %s | day_pnl=%.2f trades=%d",
                self.current_day,
                today,
                self.day_pnl,
                self.day_trades,
            )
        self.current_day = today
        self.day_pnl = 0.0
        self.day_trades = 0
        self.rejections = []

    def _persist(self) -> None:
        payload = {
            "mode": self.mode,
            "current_day": self.current_day.isoformat() if self.current_day else None,
            "day_pnl": round(self.day_pnl, 2),
            "day_trades": self.day_trades,
            "positions": {
                sym: {
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "entry_time": pos.entry_time.isoformat(),
                    "quantity": pos.quantity,
                    "tp": pos.tp,
                    "sl": pos.sl,
                    "lock_idx": pos.lock_idx,
                    "max_favorable_move": pos.max_favorable_move,
                }
                for sym, pos in self.positions.items()
            },
            "closed_trades": [
                StrategyRunner.serialize_trade(t) for t in self.closed_trades
            ],
            "rejections": list(self.rejections),
        }
        self.state_store.save(payload)
