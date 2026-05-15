"""Per-tick orchestrator. Runs identically under PAPER and LIVE, with the
side-effect surface delegated to a BrokerAdapter (PaperBroker or
DhanBroker).

Tick handling order matters:
    1. roll the rolling M5 bar for this symbol
    2. capture anchor if window open
    3. if PAPER, simulate fills off this tick and apply OCO peer cancel
       + paper-position creation
    4. on the 5-min bar boundary, drive StrategyRunner trailing using
       the closed bar's close; if SL changed and a position exists,
       call broker.modify_sl
    5. pre-square-off (square_off - 5min): cancel any unfilled OCO pairs
    6. square-off: close any open position via market order
    7. heartbeat every 5 minutes (PAPER and LIVE)

State persisted to StateStore so a mid-session restart reconciles:
    - anchors (handled by AnchorEngine)
    - OCO pairs (keyed by symbol)
    - paper positions (PaperBroker is in-memory but the orchestrator
      writes a position snapshot)
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Optional

from brokers.base import BrokerAdapter, StopOrderRequest
from brokers.paper.paper_broker import PaperBroker
from core.logger import get_logger
from core.state_store import StateStore
from core.time_utils import in_trading_hours, now_ist
from engine.anchor_engine import AnchorEngine
from engine.audit import DailyAudit, TradeSnapshot
from engine.strategy_runner import StrategyConfig, StrategyPosition, StrategyRunner
from risk.guardrails import GuardRails
from risk.position_sizer import PositionSizer
from strategy.levels import OrderLevels

logger = get_logger("orchestrator")

OCO_PAIRS_KEY = "oco_pairs"
HEARTBEAT_INTERVAL_SECONDS = 300  # 5 minutes


@dataclass
class _RollingBar:
    bar_start: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class _OcoPair:
    symbol: str
    long_order_id: str
    short_order_id: str
    long_intent: str
    short_intent: str
    levels: OrderLevels
    placed_at: str
    quantity: int = 1
    filled_side: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "long_order_id": self.long_order_id,
            "short_order_id": self.short_order_id,
            "long_intent": self.long_intent,
            "short_intent": self.short_intent,
            "levels": self.levels.__dict__,
            "placed_at": self.placed_at,
            "quantity": self.quantity,
            "filled_side": self.filled_side,
        }


@dataclass
class _SymbolMeta:
    symbol: str
    security_id: str
    exchange_segment: str
    lot_size: int = 1
    money_per_point: float = 1.0
    # sl_dist mirrors the per-symbol StrategyConfig field, but we cache it
    # here so the PositionSizer can size without reaching back into the
    # config dict for every tick.
    sl_dist: float = 0.0


class Orchestrator:
    def __init__(
        self,
        mode: str,
        broker: BrokerAdapter,
        anchor_engine: AnchorEngine,
        strategy_config: StrategyConfig,
        guardrails: GuardRails,
        symbols: list[_SymbolMeta],
        state_store: StateStore,
        audit: DailyAudit,
        no_new_trade_after: time = time(14, 30),
        pre_squareoff_buffer_minutes: int = 5,
        telegram=None,
        position_sizer: Optional[PositionSizer] = None,
        status_store: Optional[StateStore] = None,
    ) -> None:
        self.mode = mode
        self.broker = broker
        self.anchor_engine = anchor_engine
        self.runner = StrategyRunner(strategy_config)
        self.config = strategy_config
        self.guardrails = guardrails
        self.symbols = {s.symbol: s for s in symbols}
        self.state_store = state_store
        self.audit = audit
        self.no_new_trade_after = no_new_trade_after
        self.pre_squareoff_buffer = timedelta(minutes=pre_squareoff_buffer_minutes)
        self.telegram = telegram
        self.position_sizer = position_sizer
        # Pass B: live runtime status (read by api/server.py /status,
        # /heartbeat, and the Telegram poller) lives in execution_state.json
        # so the dashboard only needs to know one store.
        self.status_store = status_store or StateStore("execution_state.json")

        self.rolling_bars: dict[str, _RollingBar] = {}
        self.oco_pairs: dict[str, _OcoPair] = {}
        self.positions: dict[str, StrategyPosition] = {}
        self.day_pnl: float = 0.0
        self.day_trades: int = 0
        self._last_heartbeat_at: Optional[datetime] = None
        self._squared_off_today: set[str] = set()
        self._pre_squareoff_cancelled = False
        self.paused: bool = False  # /pause / /resume from Telegram
        self._last_tick_at: Optional[datetime] = None
        self._last_anchor_at: Optional[datetime] = None

        self._reload_oco_pairs()
        self.audit.set_mode(mode)

    # ------------------------------------------------------------ tick entry

    def on_tick(self, symbol: str, ltp: float, now: Optional[datetime] = None) -> None:
        """Single tick from the feed. The caller must already have
        decoded segment/security_id; we look them up from `self.symbols`.
        """
        if symbol not in self.symbols:
            return
        now = now or now_ist()
        self._last_tick_at = now

        # Boundary detection must run BEFORE the rolling bar is rolled,
        # so we can read the just-closed bar's close.
        self._maybe_close_bar(symbol, now)
        self._update_rolling_bar(symbol, ltp, now)
        self._maybe_capture_anchor(symbol, ltp, now)

        if isinstance(self.broker, PaperBroker):
            self._handle_paper_fills(symbol, ltp, now)

        self._maybe_place_oco_pair(symbol, now)
        self._pre_squareoff_sweep(now)
        self._squareoff_at_close(symbol, ltp, now)
        self._maybe_heartbeat(now)
        self._publish_status(now)

    def status_snapshot(self) -> dict:
        """The same dict shape we write to execution_state.json:status.

        Used by the Telegram poller via status_provider and by tests.
        """
        return {
            "ts_ist": (self._last_tick_at or now_ist()).isoformat(),
            "mode": self.mode,
            "symbols": list(self.symbols.keys()),
            "paused": self.paused,
            "healthy": self._last_tick_at is not None,
            "last_tick_at": self._last_tick_at.isoformat() if self._last_tick_at else None,
            "last_anchor_at": self._last_anchor_at.isoformat() if self._last_anchor_at else None,
            "day_pnl": round(self.day_pnl, 2),
            "day_trades": self.day_trades,
            "positions": {
                sym: {
                    "side": p.side,
                    "entry_price": p.entry_price,
                    "quantity": p.quantity,
                    "sl": p.sl,
                    "tp": p.tp,
                    "lock_idx": p.lock_idx,
                }
                for sym, p in self.positions.items()
            },
            "open_oco_pairs": sorted(self.oco_pairs.keys()),
        }

    def _publish_status(self, now: datetime) -> None:
        payload = self.status_store.load()
        payload["status"] = self.status_snapshot()
        self.status_store.save(payload)

    def shutdown(self) -> None:
        """Graceful shutdown.

          - LIVE: square off open positions, cancel pending orders.
          - PAPER: flush audit triplet.

        Called from the SIGTERM handler in run_dhan.py.
        """
        if self.mode == "LIVE":
            for symbol in list(self.positions.keys()):
                self.broker.close_position(symbol)
            for pair in list(self.oco_pairs.values()):
                self.broker.cancel_order(pair.long_order_id)
                self.broker.cancel_order(pair.short_order_id)
        # Final status write so the dashboard reflects the shutdown.
        self._publish_status(now_ist())
        self.audit.flush(state_store=self.state_store)

    # ----------------------------------------------------------- rolling bar

    def _update_rolling_bar(self, symbol: str, ltp: float, now: datetime) -> None:
        bar_start = _bar_start(now)
        existing = self.rolling_bars.get(symbol)
        if existing is None or existing.bar_start != bar_start:
            self.rolling_bars[symbol] = _RollingBar(
                bar_start=bar_start, open=ltp, high=ltp, low=ltp, close=ltp,
            )
            return
        existing.high = max(existing.high, ltp)
        existing.low = min(existing.low, ltp)
        existing.close = ltp

    # ----------------------------------------------------------- anchor

    def _maybe_capture_anchor(self, symbol: str, ltp: float, now: datetime) -> None:
        if not self.anchor_engine.should_capture_anchor(symbol, now=now):
            return
        bar = self.rolling_bars.get(symbol)
        # Anchor source = M5 bar open per Phase 3 default. Fall back to
        # the current tick if we have no rolling bar yet (first tick
        # inside the capture window).
        source_open = bar.open if bar is not None else ltp
        source_bar_time = bar.bar_start if bar is not None else now
        record = self.anchor_engine.capture_anchor(
            symbol=symbol,
            anchor_price=source_open,
            source_bar_time=source_bar_time,
            now=now,
        )
        self._last_anchor_at = now
        self.audit.set_anchor(symbol, record.anchor_price)
        self.audit.emit("anchor_captured", symbol, {
            "anchor_price": record.anchor_price,
            "source_bar_time": record.source_bar_time.isoformat(),
            "captured_at": record.captured_at.isoformat(),
        })

    # ----------------------------------------------------------- OCO entry

    def _maybe_place_oco_pair(self, symbol: str, now: datetime) -> None:
        if symbol in self.oco_pairs:
            return
        if symbol in self.positions:
            return
        if now.time() >= self.no_new_trade_after:
            return
        if self.paused:
            self.audit.emit("guardrail_blocked", symbol, {"reason": "paused_via_telegram"})
            return
        record = self.anchor_engine.get_anchor(symbol, now=now)
        if record is None:
            return

        # Guardrails — same checks ExecutionEngine ran in Phase 1.
        guard = self.guardrails.validate_trade_count(self.day_trades)
        if not guard.allowed:
            self.audit.emit("guardrail_blocked", symbol, {"reason": guard.reason})
            return
        guard = self.guardrails.validate_daily_loss(self.day_pnl)
        if not guard.allowed:
            self.audit.emit("guardrail_blocked", symbol, {"reason": guard.reason})
            return

        levels = self.runner.build_levels(record.anchor_price)
        self.audit.emit("levels_computed", symbol, {
            "anchor": levels.anchor,
            "long_entry": levels.long_entry,
            "long_sl": levels.long_sl,
            "long_tp": levels.long_tp,
            "short_entry": levels.short_entry,
            "short_sl": levels.short_sl,
            "short_tp": levels.short_tp,
        })

        meta = self.symbols[symbol]
        quantity = self._size_for(symbol, meta)
        if quantity == 0:
            return

        long_intent = f"{symbol}-{now.date().isoformat()}-LONG-STOP"
        short_intent = f"{symbol}-{now.date().isoformat()}-SHORT-STOP"
        long_order = self.broker.place_stop_order(StopOrderRequest(
            symbol=symbol,
            security_id=meta.security_id,
            side="LONG",
            quantity=quantity,
            trigger_price=levels.long_entry,
            exchange_segment=meta.exchange_segment,
        ))
        short_order = self.broker.place_stop_order(StopOrderRequest(
            symbol=symbol,
            security_id=meta.security_id,
            side="SHORT",
            quantity=quantity,
            trigger_price=levels.short_entry,
            exchange_segment=meta.exchange_segment,
        ))
        if isinstance(self.broker, PaperBroker):
            self.broker.link_intent(long_order.order_id, long_intent)
            self.broker.link_intent(short_order.order_id, short_intent)

        pair = _OcoPair(
            symbol=symbol,
            long_order_id=long_order.order_id,
            short_order_id=short_order.order_id,
            long_intent=long_intent,
            short_intent=short_intent,
            levels=levels,
            placed_at=now.isoformat(),
            quantity=quantity,
        )
        self.oco_pairs[symbol] = pair
        self._persist_oco_pairs()
        self.audit.emit("oco_pair_placed", symbol, {
            "long_order_id": long_order.order_id,
            "short_order_id": short_order.order_id,
            "long_entry": levels.long_entry,
            "short_entry": levels.short_entry,
        })

    def _size_for(self, symbol: str, meta: _SymbolMeta) -> int:
        """Compute order quantity for an OCO leg.

        With no PositionSizer attached, fall back to one lot (the Phase 5
        default) so this method is a pure no-op for callers that don't
        opt into capital-based sizing.

        With a sizer attached, refuse the trade and emit a
        `guardrail_blocked` audit event when the sizer returns 0 — the
        sl_dist exceeds the per-trade risk budget for even a single lot
        and the caller should NOT under-trade against the configured
        risk floor.
        """
        if self.position_sizer is None:
            return meta.lot_size
        sl_dist = meta.sl_dist or self.config.sl_dist
        decision = self.position_sizer.size(
            sl_dist=sl_dist,
            money_per_point=meta.money_per_point,
            lot_size=meta.lot_size,
        )
        if decision.skipped:
            logger.warning(
                "position_size_zero",
                extra={
                    "symbol": symbol,
                    "capital": self.position_sizer.capital,
                    "risk_budget": decision.risk_budget,
                    "per_lot_risk": decision.per_lot_risk,
                    "sl_dist": sl_dist,
                    "money_per_point": meta.money_per_point,
                    "lot_size": meta.lot_size,
                    "sizer_reason": decision.skipped_reason,
                },
            )
            self.audit.emit("guardrail_blocked", symbol, {
                "reason": "position_size_zero",
                "sizer_reason": decision.skipped_reason,
                "capital": self.position_sizer.capital,
                "max_risk_pct": self.position_sizer.max_risk_pct,
                "sl_dist": sl_dist,
                "money_per_point": meta.money_per_point,
                "lot_size": meta.lot_size,
            })
            return 0
        return decision.quantity

    # ------------------------------------------------- paper fill handling

    def _handle_paper_fills(self, symbol: str, ltp: float, now: datetime) -> None:
        meta = self.symbols.get(symbol)
        if meta is None:
            return
        assert isinstance(self.broker, PaperBroker)
        fills = self.broker.process_tick(meta.security_id, ltp)
        for fill in fills:
            self._on_order_fill(symbol, fill.order_id, fill.side, fill.trigger_price or 0.0, now)

    def _on_order_fill(self, symbol: str, order_id: str, side, fill_price: float, now: datetime) -> None:
        pair = self.oco_pairs.get(symbol)
        if pair is None:
            return
        peer_id = (
            pair.short_order_id if order_id == pair.long_order_id else pair.long_order_id
        )
        pair.filled_side = side
        self.broker.cancel_oco_peer(filled_order_id=order_id, peer_order_id=peer_id)
        self.audit.emit("order_filled", symbol, {"order_id": order_id, "side": side, "fill_price": fill_price})
        self.audit.emit("peer_cancelled", symbol, {"order_id": peer_id})

        meta = self.symbols[symbol]
        if side == "LONG":
            tp = pair.levels.long_tp
            sl = pair.levels.long_sl
        else:
            tp = pair.levels.short_tp
            sl = pair.levels.short_sl

        position = StrategyPosition(
            symbol=symbol,
            side=side,
            entry_price=fill_price,
            entry_time=now,
            quantity=pair.quantity,
            tp=tp,
            sl=sl,
        )
        self.positions[symbol] = position
        # Resting TP/SL bookkeeping at the broker (modify_sl uses
        # position_id=symbol for PaperBroker).
        self.broker.modify_sl(symbol, sl)
        self.broker.modify_tp(symbol, tp)
        del self.oco_pairs[symbol]
        self._persist_oco_pairs()

    # ----------------------------------------------- 5-min bar close trail

    def _maybe_close_bar(self, symbol: str, now: datetime) -> None:
        """Detect a transition into a new 5-min window and use the just-
        closed window's close to drive lagged trailing.
        """
        bar = self.rolling_bars.get(symbol)
        if bar is None:
            return
        bar_start = _bar_start(now)
        if bar_start == bar.bar_start:
            return
        # We've moved past the bar's window. Trail position using
        # bar.close (the lagged-close from Phase 2 fix).
        position = self.positions.get(symbol)
        if position is None:
            return
        prev_sl = position.sl
        self.runner.update_trailing_from_close(position, bar.close)
        if position.sl != prev_sl:
            self.broker.modify_sl(symbol, position.sl)
            self.audit.emit("sl_modified", symbol, {
                "prev_sl": prev_sl,
                "new_sl": position.sl,
                "lock_idx": position.lock_idx,
                "close": bar.close,
            })

    # ----------------------------------------------------- square-off

    def _pre_squareoff_sweep(self, now: datetime) -> None:
        if self._pre_squareoff_cancelled:
            return
        cutoff = _today_at(self.config.square_off_time, now) - self.pre_squareoff_buffer
        if now < cutoff:
            return
        self._pre_squareoff_cancelled = True
        for symbol, pair in list(self.oco_pairs.items()):
            self.broker.cancel_order(pair.long_order_id)
            self.broker.cancel_order(pair.short_order_id)
            self.audit.emit("oco_pair_cancelled_pre_squareoff", symbol, {
                "long_order_id": pair.long_order_id,
                "short_order_id": pair.short_order_id,
            })
            del self.oco_pairs[symbol]
        self._persist_oco_pairs()

    def _squareoff_at_close(self, symbol: str, ltp: float, now: datetime) -> None:
        cutoff = _today_at(self.config.square_off_time, now)
        if now < cutoff:
            return
        if symbol in self._squared_off_today:
            return
        position = self.positions.get(symbol)
        if position is None:
            self._squared_off_today.add(symbol)
            return

        if isinstance(self.broker, PaperBroker):
            points = self.broker.force_close(symbol, ltp) or 0.0
            money_pnl = round(points * position.quantity, 2)
        else:
            self.broker.close_position(symbol)
            if position.side == "LONG":
                points = round(ltp - position.entry_price, 2)
            else:
                points = round(position.entry_price - ltp, 2)
            money_pnl = round(points * position.quantity, 2)

        self.day_pnl += money_pnl
        self.day_trades += 1
        self._squared_off_today.add(symbol)
        del self.positions[symbol]

        self.audit.set_square_off_time(now.isoformat())
        self.audit.emit("square_off", symbol, {"exit_price": ltp, "points_pnl": points, "money_pnl": money_pnl})
        self.audit.record_trade(TradeSnapshot(
            symbol=symbol,
            side=position.side,
            entry_price=position.entry_price,
            exit_price=ltp,
            points_pnl=points,
            money_pnl=money_pnl,
            exit_reason="SQUARE_OFF",
        ))

    # --------------------------------------------------------- heartbeat

    def _maybe_heartbeat(self, now: datetime) -> None:
        if self.telegram is None:
            return
        if self._last_heartbeat_at is not None:
            if (now - self._last_heartbeat_at).total_seconds() < HEARTBEAT_INTERVAL_SECONDS:
                return
        self._last_heartbeat_at = now
        message = (
            f"[{self.mode}] {now.strftime('%H:%M IST')} | "
            f"symbols={list(self.symbols.keys())} | "
            f"positions={list(self.positions.keys())} | "
            f"day_pnl={self.day_pnl:.2f}"
        )
        try:
            self.telegram.send(message)
            self.audit.emit("heartbeat", None, {"message": message})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Heartbeat send failed: %s", exc)

    # --------------------------------------------------------- persistence

    def _persist_oco_pairs(self) -> None:
        payload = self.state_store.load()
        payload[OCO_PAIRS_KEY] = {sym: p.to_dict() for sym, p in self.oco_pairs.items()}
        self.state_store.save(payload)

    def _reload_oco_pairs(self) -> None:
        payload = self.state_store.load()
        raw = payload.get(OCO_PAIRS_KEY, {}) or {}
        for symbol, p in raw.items():
            levels_raw = p.get("levels", {}) or {}
            self.oco_pairs[symbol] = _OcoPair(
                symbol=p["symbol"],
                long_order_id=p["long_order_id"],
                short_order_id=p["short_order_id"],
                long_intent=p.get("long_intent", ""),
                short_intent=p.get("short_intent", ""),
                levels=OrderLevels(**levels_raw),
                placed_at=p.get("placed_at", ""),
                quantity=int(p.get("quantity", 1)),
                filled_side=p.get("filled_side"),
            )


def _bar_start(now: datetime) -> datetime:
    """Round down to the most recent 5-minute boundary in IST."""
    minute_floor = (now.minute // 5) * 5
    return now.replace(minute=minute_floor, second=0, microsecond=0)


def _today_at(target: time, now: datetime) -> datetime:
    return now.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
