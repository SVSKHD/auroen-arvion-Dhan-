from backtest.backtest_engine import BacktestEngine
from core.logger import get_logger

logger = get_logger("symbol_scorer")


class SymbolScorer:
    def __init__(self) -> None:
        self.backtester = BacktestEngine()

    def score(
        self,
        symbol: str,
        csv_path: str,
        trigger_dist: float,
        tp_dist: float,
        sl_dist: float,
    ) -> dict:
        logger.info("Backtesting symbol | %s", symbol)

        result = self.backtester.run(
            csv_path=csv_path,
            trigger_dist=trigger_dist,
            tp_dist=tp_dist,
            sl_dist=sl_dist,
        )

        return {
            "symbol": symbol,
            **result,
        }
