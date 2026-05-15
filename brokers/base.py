from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Optional


Side = Literal["LONG", "SHORT"]


@dataclass
class BrokerOrder:
    order_id: str
    side: Side
    status: str
    price: float
    quantity: int


@dataclass
class BrokerPosition:
    position_id: str
    side: Side
    entry_price: float
    quantity: int
    sl: Optional[float] = None
    tp: Optional[float] = None


class BrokerAdapter(ABC):
    @abstractmethod
    def get_ltp(self, symbol: str) -> float:
        raise NotImplementedError

    @abstractmethod
    def place_market_order(self, symbol: str, side: Side, quantity: int) -> BrokerOrder:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_open_position(self, symbol: str) -> Optional[BrokerPosition]:
        raise NotImplementedError

    @abstractmethod
    def modify_sl(self, position_id: str, new_sl: float) -> bool:
        raise NotImplementedError

    @abstractmethod
    def close_position(self, position_id: str) -> bool:
        raise NotImplementedError
