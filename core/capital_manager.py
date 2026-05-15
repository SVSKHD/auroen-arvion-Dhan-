from config.strategy_config import SUPPORTED_SYMBOLS


class CapitalManager:
    def __init__(self, capital: float) -> None:
        self.capital = capital

    def allowed_symbols(self) -> list[dict]:
        return [
            symbol
            for symbol in SUPPORTED_SYMBOLS
            if self.capital >= symbol["min_capital"]
        ]
