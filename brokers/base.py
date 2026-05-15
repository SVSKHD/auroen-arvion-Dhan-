from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Optional


Side = Literal["LONG", "SHORT"]
OrderKind = Literal["MARKET", "LIMIT", "STOP", "STOP_LIMIT", "BRACKET", "OCO"]
OrderStatus = Literal[
    "PENDING",
    "OPEN",
    "FILLED",
    "PARTIALLY_FILLED",
    "CANCELLED",
    "REJECTED",
    "CLOSED",
    "UNKNOWN",
]


@dataclass
class BrokerOrder:
    order_id: str
    side: Side
    status: OrderStatus
    price: float
    quantity: int
    order_kind: OrderKind = "MARKET"
    trigger_price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    raw: Optional[dict] = None


@dataclass
class BrokerPosition:
    position_id: str
    symbol: str
    side: Side
    entry_price: float
    quantity: int
    sl: Optional[float] = None
    tp: Optional[float] = None
    pnl: Optional[float] = None
    raw: Optional[dict] = None


@dataclass
class BrokerAccount:
    client_id: str
    available_balance: float
    net_balance: Optional[float] = None
    used_margin: Optional[float] = None
    raw: Optional[dict] = None


@dataclass
class StopOrderRequest:
    symbol: str
    security_id: str
    side: Side
    quantity: int
    trigger_price: float
    limit_price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    product_type: str = "INTRADAY"
    exchange_segment: str = "NSE_FNO"


class BrokerAdapter(ABC):
    """Broker-neutral contract used by the strategy engine.

    The strategy is an anchor-breakout system, so the interface must support
    resting stop orders and OCO-style cancellation. Market orders alone are not
    enough for this bot.
    """

    @abstractmethod
    def get_account(self) -> BrokerAccount:
        raise NotImplementedError

    @abstractmethod
    def get_ltp(self, symbol: str) -> float:
        raise NotImplementedError

    @abstractmethod
    def place_stop_order(self, request: StopOrderRequest) -> BrokerOrder:
        raise NotImplementedError

    @abstractmethod
    def place_market_order(self, symbol: str, side: Side, quantity: int) -> BrokerOrder:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_order(self, order_id: str) -> BrokerOrder:
        raise NotImplementedError

    @abstractmethod
    def get_open_orders(self, symbol: Optional[str] = None) -> list[BrokerOrder]:
        raise NotImplementedError

    @abstractmethod
    def get_open_position(self, symbol: str) -> Optional[BrokerPosition]:
        raise NotImplementedError

    @abstractmethod
    def get_open_positions(self) -> list[BrokerPosition]:
        raise NotImplementedError

    @abstractmethod
    def modify_sl(self, position_id: str, new_sl: float) -> bool:
        raise NotImplementedError

    @abstractmethod
    def modify_tp(self, position_id: str, new_tp: float) -> bool:
        raise NotImplementedError

    @abstractmethod
    def close_position(self, position_id: str) -> bool:
        raise NotImplementedError

    def cancel_oco_peer(self, filled_order_id: str, peer_order_id: str) -> bool:
        """Default OCO helper: when one side fills, cancel the opposite side."""
        _ = filled_order_id
        return self.cancel_order(peer_order_id)
