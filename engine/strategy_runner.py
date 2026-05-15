from dataclasses import asdict, dataclass
from datetime import datetime, time
from typing import Literal, Optional

from strategy.levels import OrderLevels, build_order_levels

Side = Literal["LONG", "SHORT"]
ExitReason = Literal["TP", "SL", "TRAIL", "SQUARE_OFF"]


@dataclass
class StrategyConfig:
    trigger_dist: float
    tp_dist: float
    sl_dist: float
    lock_step: float
    lock_steps_count: int
    anchor_time: time = time(9, 15)
    square_off_time: time = time(15, 15)
    tick_size: float = 0.05
    sl_first_on_ambiguous_bar: bool = True


@dataclass
class StrategyPosition:
    symbol: str
    side: Side
    entry_price: float
    entry_time: datetime
    quantity: int
    tp: float
    sl: float
    lock_idx: int = -1
    max_favorable_move: float = 0.0


@dataclass
class StrategyTradeResult:
    symbol: str
    side: Side
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    quantity: int
    points_pnl: float
    money_pnl: float
    exit_reason: ExitReason
    lock_idx: int


class StrategyRunner:
    """Single source of truth for anchor-breakout behavior.

    BACKTEST, PAPER, and LIVE should all use this class for signal, TP/SL,
    trailing-lock, and square-off rules. Only price source and order sink differ.
    """

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def build_levels(self, anchor_price: float) -> OrderLevels:
        return build_order_levels(
            anchor_price=anchor_price,
            trigger_dist=self.config.trigger_dist,
            tp_dist=self.config.tp_dist,
            sl_dist=self.config.sl_dist,
            tick_size=self.config.tick_size,
        )

    def detect_entry(
        self,
        symbol: str,
        levels: OrderLevels,
        bar_time: datetime,
        high: float,
        low: float,
        quantity: int,
    ) -> Optional[StrategyPosition]:
        long_hit = high >= levels.long_entry
        short_hit = low <= levels.short_entry

        if not long_hit and not short_hit:
            return None

        # Same candle hit both triggers: avoid fantasy edge. Skip trade because
        # OHLC candles cannot prove which side triggered first.
        if long_hit and short_hit:
            return None

        if long_hit:
            return StrategyPosition(
                symbol=symbol,
                side="LONG",
                entry_price=levels.long_entry,
                entry_time=bar_time,
                quantity=quantity,
                tp=levels.long_tp,
                sl=levels.long_sl,
            )

        return StrategyPosition(
            symbol=symbol,
            side="SHORT",
            entry_price=levels.short_entry,
            entry_time=bar_time,
            quantity=quantity,
            tp=levels.short_tp,
            sl=levels.short_sl,
        )

    def update_trailing(self, position: StrategyPosition, high: float, low: float) -> None:
        if position.side == "LONG":
            favorable = high - position.entry_price
        else:
            favorable = position.entry_price - low

        if favorable < self.config.lock_step:
            return

        position.max_favorable_move = max(position.max_favorable_move, favorable)
        new_idx = int(position.max_favorable_move // self.config.lock_step) - 1
        new_idx = min(new_idx, self.config.lock_steps_count - 1)

        if new_idx <= position.lock_idx:
            return

        position.lock_idx = new_idx

        # Safer rule: first lock moves to breakeven; next locks secure profit.
        lock_profit = self.config.lock_step * position.lock_idx

        if position.side == "LONG":
            position.sl = round(position.entry_price + lock_profit, 2)
        else:
            position.sl = round(position.entry_price - lock_profit, 2)

    def evaluate_exit(
        self,
        position: StrategyPosition,
        bar_time: datetime,
        high: float,
        low: float,
        close: float,
    ) -> Optional[StrategyTradeResult]:
        self.update_trailing(position, high=high, low=low)

        if position.side == "LONG":
            sl_hit = low <= position.sl
            tp_hit = high >= position.tp

            if sl_hit and tp_hit:
                exit_price = position.sl if self.config.sl_first_on_ambiguous_bar else position.tp
                reason: ExitReason = "SL" if self.config.sl_first_on_ambiguous_bar else "TP"
                return self._result(position, exit_price, bar_time, reason)

            if sl_hit:
                reason = "TRAIL" if position.lock_idx >= 0 and position.sl >= position.entry_price else "SL"
                return self._result(position, position.sl, bar_time, reason)

            if tp_hit:
                return self._result(position, position.tp, bar_time, "TP")

            if bar_time.time() >= self.config.square_off_time:
                return self._result(position, close, bar_time, "SQUARE_OFF")

            return None

        sl_hit = high >= position.sl
        tp_hit = low <= position.tp

        if sl_hit and tp_hit:
            exit_price = position.sl if self.config.sl_first_on_ambiguous_bar else position.tp
            reason = "SL" if self.config.sl_first_on_ambiguous_bar else "TP"
            return self._result(position, exit_price, bar_time, reason)

        if sl_hit:
            reason = "TRAIL" if position.lock_idx >= 0 and position.sl <= position.entry_price else "SL"
            return self._result(position, position.sl, bar_time, reason)

        if tp_hit:
            return self._result(position, position.tp, bar_time, "TP")

        if bar_time.time() >= self.config.square_off_time:
            return self._result(position, close, bar_time, "SQUARE_OFF")

        return None

    def _result(
        self,
        position: StrategyPosition,
        exit_price: float,
        exit_time: datetime,
        reason: ExitReason,
    ) -> StrategyTradeResult:
        if position.side == "LONG":
            points_pnl = exit_price - position.entry_price
        else:
            points_pnl = position.entry_price - exit_price

        money_pnl = points_pnl * position.quantity

        return StrategyTradeResult(
            symbol=position.symbol,
            side=position.side,
            entry_price=round(position.entry_price, 2),
            exit_price=round(exit_price, 2),
            entry_time=position.entry_time,
            exit_time=exit_time,
            quantity=position.quantity,
            points_pnl=round(points_pnl, 2),
            money_pnl=round(money_pnl, 2),
            exit_reason=reason,
            lock_idx=position.lock_idx,
        )

    @staticmethod
    def serialize_trade(trade: StrategyTradeResult) -> dict:
        data = asdict(trade)
        data["entry_time"] = trade.entry_time.isoformat()
        data["exit_time"] = trade.exit_time.isoformat()
        return data
