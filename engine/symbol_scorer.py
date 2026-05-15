from backtest.backtest_engine import BacktestEngine
from core.logger import get_logger
from engine.strategy_runner import StrategyConfig

logger = get_logger("symbol_scorer")


class SymbolScorer:
    def __init__(self, config: StrategyConfig) -> None:
        self.backtester = BacktestEngine(config)

    def score(self, symbol: str, csv_path: str, quantity: int = 1) -> dict:
        logger.info("Backtesting symbol | %s", symbol)
        result = self.backtester.run(csv_path=csv_path, quantity=quantity)
        return {"symbol": symbol, **result}
