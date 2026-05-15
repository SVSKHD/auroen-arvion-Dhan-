from core.logger import get_logger
from core.paper_engine import PaperEngine

logger = get_logger("paper_runner")


class PaperRunner:
    def __init__(self) -> None:
        self.paper_engine = PaperEngine()

    def simulate_symbol(
        self,
        symbol: str,
        anchor_price: float,
        current_price: float,
        trigger_dist: float,
        tp_dist: float,
        sl_dist: float,
    ) -> dict:
        logger.info("Paper simulate | symbol=%s", symbol)

        return self.paper_engine.simulate(
            symbol=symbol,
            anchor_price=anchor_price,
            current_price=current_price,
            trigger_dist=trigger_dist,
            tp_dist=tp_dist,
            sl_dist=sl_dist,
        )
