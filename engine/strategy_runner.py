from dataclasses import asdict, dataclass
from datetime import datetime, time
from typing import Iterable, Literal, Optional

from strategy.levels import OrderLevels, build_order_levels

Side = Literal["LONG", "SHORT"]
ExitReason = Literal["TP", "SL", "TRAIL", "SQUARE_OFF"]
BarResolution = Literal["M5", "M1"]


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
    bar_resolution: BarResolution = "M5"


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


@dataclass
class M1Bar:
    """One-minute bar used for intra-M5 replay."""
    time: datetime
    open: float
    high: float
    low: float
    close: float


class StrategyRunner:
    """Single source of truth for anchor-breakout behavior.

    BACKTEST, PAPER, and LIVE all route through this class.

    Phase 2 fix: trailing no longer ratchets off intra-bar high before exit
    check on the same bar (look-ahead bug). The new model:

    - Exits are evaluated FIRST against the SL that was valid coming into
      the bar.
    - Then the SL ratchets one step at most, using a single reference price
      (bar close in M5 mode, sub-bar close in M1 mode). One-step-per-ratchet
      cap is conservative and makes the trailing path-safe.
    - For tighter accuracy, M1 mode is available: call replay_intra_bar
      with the M1 sub-bars that fall inside the M5 bar.
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

        # Same bar hit both triggers: cannot prove sequence from OHLC.
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

    def update_trailing(self, position: StrategyPosition, ratchet_price: float) -> None:
        """Advance lock at most one step using ratchet_price (typically a close).

        Public API change (Phase 2): was update_trailing(position, high, low).
        Callers must pass a single reference price — the bar close in M5 mode
        or sub-bar close in M1 mode. One-step advancement is conservative;
        repeated calls (per sub-bar) walk the lock forward.

        Lock convention:
          idx == -1 -> no lock (SL at initial stop distance)
          idx ==  0 -> SL at breakeven (entry)
          idx ==  k -> SL at entry + k * lock_step (LONG) or entry - k * lock_step (SHORT)
        """
        if position.side == "LONG":
            favorable = ratchet_price - position.entry_price
        else:
            favorable = position.entry_price - ratchet_price

        if favorable < self.config.lock_step:
            return

        position.max_favorable_move = max(position.max_favorable_move, favorable)

        next_idx = position.lock_idx + 1
        if next_idx >= self.config.lock_steps_count:
            return

        position.lock_idx = next_idx
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
        """Evaluate exit for one M5 bar.

        In M5 mode this also ratchets trailing off the bar close AFTER the
        exit check. In M1 mode it only checks exits — call replay_intra_bar
        if you have M1 data for the same M5 window.
        """
        result = self._check_exits(position, bar_time, high, low, close)
        if result is not None:
            return result

        if self.config.bar_resolution == "M5":
            self.update_trailing(position, ratchet_price=close)

        return None

    def replay_intra_bar(
        self,
        position: StrategyPosition,
        m1_bars: Iterable[M1Bar],
    ) -> Optional[StrategyTradeResult]:
        """Drive exit + trailing across M1 sub-bars that compose one M5 bar.

        Per sub-bar: exit check against current SL, then ratchet off the
        sub-bar close. First sub-bar SL hit / TP hit / square-off wins.
        """
        for sub in m1_bars:
            result = self._check_exits(position, sub.time, sub.high, sub.low, sub.close)
            if result is not None:
                return result
            self.update_trailing(position, ratchet_price=sub.close)
        return None

    def _check_exits(
        self,
        position: StrategyPosition,
        bar_time: datetime,
        high: float,
        low: float,
        close: float,
    ) -> Optional[StrategyTradeResult]:
        if position.side == "LONG":
            sl_hit = low <= position.sl
            tp_hit = high >= position.tp

            if sl_hit and tp_hit:
                exit_price = position.sl if self.config.sl_first_on_ambiguous_bar else position.tp
                reason: ExitReason = "SL" if self.config.sl_first_on_ambiguous_bar else "TP"
                if reason == "SL" and position.lock_idx >= 0 and position.sl >= position.entry_price:
                    reason = "TRAIL"
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
            if reason == "SL" and position.lock_idx >= 0 and position.sl <= position.entry_price:
                reason = "TRAIL"
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
