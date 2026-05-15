from brokers.base import BrokerAdapter
from config.strategy_config import MODE
from core.logger import get_logger
from core.paper_engine import PaperEngine

logger = get_logger("execution_engine")


class ExecutionEngine:
    def __init__(self, broker: BrokerAdapter | None = None) -> None:
        self.mode = MODE.upper()
        self.broker = broker
        self.paper_engine = PaperEngine()

    def execute_signal(
        self,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
    ) -> dict:
        logger.info(
            "Executing signal | mode=%s | symbol=%s | side=%s",
            self.mode,
            symbol,
            side,
        )

        if self.mode == "PAPER":
            return {
                "mode": "PAPER",
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "status": "SIMULATED",
            }

        if self.mode == "LIVE":
            if self.broker is None:
                raise RuntimeError("LIVE mode requires broker")

            order = self.broker.place_market_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
            )

            return {
                "mode": "LIVE",
                "symbol": symbol,
                "side": side,
                "order_id": order.order_id,
                "status": order.status,
            }

        raise ValueError(f"Unknown mode: {self.mode}")
