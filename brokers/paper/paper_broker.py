"""In-memory broker stub used by PAPER mode.

PAPER and LIVE go through the same `BrokerAdapter` contract in the
orchestrator. The only difference is the side effect: PAPER records
orders in memory and simulates fills off the live tick stream; LIVE
hands orders to Dhan and waits for real fills.

`process_tick(security_id, exchange_segment, ltp)` is the PAPER-only
hook the orchestrator calls after each tick. It walks any resting stop
orders for that security and returns a list of orders that would have
filled at this LTP. The orchestrator then performs OCO peer cancel and
position bookkeeping just like it would on a real fill.
"""

import itertools
from dataclasses import dataclass, field
from typing import Optional

from brokers.base import (
    BrokerAccount,
    BrokerAdapter,
    BrokerOrder,
    BrokerPosition,
    OrderKind,
    OrderStatus,
    Side,
    StopOrderRequest,
)
from core.logger import get_logger

logger = get_logger("paper_broker")


@dataclass
class _RestingOrder:
    order_id: str
    symbol: str
    security_id: str
    exchange_segment: str
    side: Side
    quantity: int
    order_kind: OrderKind
    trigger_price: Optional[float]
    status: OrderStatus = "PENDING"
    intent_id: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass
class _PaperPosition:
    symbol: str
    security_id: str
    side: Side
    entry_price: float
    quantity: int
    sl: Optional[float] = None
    tp: Optional[float] = None


class PaperBroker(BrokerAdapter):
    """Simulated broker. State is intentionally in-memory only — the
    orchestrator persists everything it needs to recover (anchors,
    positions, OCO pairs) through StateStore.
    """

    def __init__(self, starting_capital: float = 250000.0) -> None:
        self.capital = starting_capital
        self._orders: dict[str, _RestingOrder] = {}
        self._positions: dict[str, _PaperPosition] = {}  # keyed by symbol
        self._order_counter = itertools.count(1)

    # ---------------------------------------------------------- BrokerAdapter

    def get_account(self) -> BrokerAccount:
        return BrokerAccount(client_id="PAPER", available_balance=self.capital)

    def get_ltp(self, symbol: str) -> float:
        raise NotImplementedError(
            "PaperBroker does not know prices; orchestrator feeds ticks in."
        )

    def place_stop_order(self, request: StopOrderRequest) -> BrokerOrder:
        order_id = f"paper-{next(self._order_counter)}"
        resting = _RestingOrder(
            order_id=order_id,
            symbol=request.symbol,
            security_id=str(request.security_id),
            exchange_segment=request.exchange_segment,
            side=request.side,
            quantity=request.quantity,
            order_kind="STOP",
            trigger_price=float(request.trigger_price),
            status="OPEN",
        )
        self._orders[order_id] = resting
        return self._to_broker_order(resting)

    def place_market_order(self, symbol: str, side: Side, quantity: int) -> BrokerOrder:
        """Immediate fill at the last submitted LTP for the symbol's
        security_id. In PAPER mode this is only used for square-off; the
        orchestrator passes the symbol's last known tick before calling.
        """
        order_id = f"paper-{next(self._order_counter)}"
        position = self._positions.get(symbol)
        fill_price = position.entry_price if position is not None else 0.0
        # If we have a position we are closing, mark it closed at the
        # exit price the caller supplied via _force_close (below). The
        # simple market_order path is the unstructured case.
        resting = _RestingOrder(
            order_id=order_id,
            symbol=symbol,
            security_id="",
            exchange_segment="",
            side=side,
            quantity=quantity,
            order_kind="MARKET",
            trigger_price=None,
            status="FILLED",
        )
        self._orders[order_id] = resting
        return self._to_broker_order(resting, price=fill_price)

    def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order is None or order.status != "OPEN":
            return False
        order.status = "CANCELLED"
        return True

    def get_order(self, order_id: str) -> BrokerOrder:
        order = self._orders.get(order_id)
        if order is None:
            return BrokerOrder(order_id=order_id, side="LONG", status="UNKNOWN", price=0.0, quantity=0)
        return self._to_broker_order(order)

    def get_open_orders(self, symbol: Optional[str] = None) -> list[BrokerOrder]:
        out = []
        for o in self._orders.values():
            if o.status != "OPEN":
                continue
            if symbol is not None and o.symbol != symbol:
                continue
            out.append(self._to_broker_order(o))
        return out

    def get_open_position(self, symbol: str) -> Optional[BrokerPosition]:
        p = self._positions.get(symbol)
        return self._to_broker_position(p) if p else None

    def get_open_positions(self) -> list[BrokerPosition]:
        return [self._to_broker_position(p) for p in self._positions.values()]

    def modify_sl(self, position_id: str, new_sl: float) -> bool:
        position = self._positions.get(position_id)
        if position is None:
            return False
        position.sl = float(new_sl)
        return True

    def modify_tp(self, position_id: str, new_tp: float) -> bool:
        position = self._positions.get(position_id)
        if position is None:
            return False
        position.tp = float(new_tp)
        return True

    def close_position(self, position_id: str) -> bool:
        if position_id not in self._positions:
            return False
        del self._positions[position_id]
        return True

    # ---------------------------------------------------------- PAPER hooks

    def process_tick(self, security_id: str, ltp: float) -> list[BrokerOrder]:
        """Fill resting stops whose triggers are crossed by this tick.

        BUY stop fills when ltp >= trigger; SELL stop fills when
        ltp <= trigger. Returns the fills so the orchestrator can do OCO
        peer cancel and create the paper position.
        """
        fills: list[BrokerOrder] = []
        for order in list(self._orders.values()):
            if order.status != "OPEN":
                continue
            if order.security_id != str(security_id):
                continue
            if order.order_kind != "STOP":
                continue
            trigger = order.trigger_price or 0.0
            crossed = (
                ltp >= trigger if order.side == "LONG" else ltp <= trigger
            )
            if not crossed:
                continue
            order.status = "FILLED"
            # Create or merge into a paper position.
            existing = self._positions.get(order.symbol)
            if existing is None:
                self._positions[order.symbol] = _PaperPosition(
                    symbol=order.symbol,
                    security_id=order.security_id,
                    side=order.side,
                    entry_price=trigger,
                    quantity=order.quantity,
                )
            fills.append(self._to_broker_order(order, price=trigger))
        return fills

    def force_close(self, symbol: str, exit_price: float) -> Optional[float]:
        """Square-off helper used by the orchestrator at end-of-day.

        Returns the realized PnL in points if the position existed.
        """
        position = self._positions.pop(symbol, None)
        if position is None:
            return None
        if position.side == "LONG":
            return round(exit_price - position.entry_price, 2)
        return round(position.entry_price - exit_price, 2)

    def link_intent(self, order_id: str, intent_id: str) -> None:
        order = self._orders.get(order_id)
        if order is not None:
            order.intent_id = intent_id

    # ---------------------------------------------------------- conversions

    def _to_broker_order(self, o: _RestingOrder, price: float = 0.0) -> BrokerOrder:
        return BrokerOrder(
            order_id=o.order_id,
            side=o.side,
            status=o.status,
            price=price,
            quantity=o.quantity,
            order_kind=o.order_kind,
            trigger_price=o.trigger_price,
            raw={"symbol": o.symbol, "security_id": o.security_id, "intent_id": o.intent_id},
        )

    def _to_broker_position(self, p: _PaperPosition) -> BrokerPosition:
        return BrokerPosition(
            position_id=p.symbol,
            symbol=p.symbol,
            side=p.side,
            entry_price=p.entry_price,
            quantity=p.quantity,
            sl=p.sl,
            tp=p.tp,
        )
