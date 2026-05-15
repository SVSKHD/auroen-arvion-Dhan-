from core.capital_manager import CapitalManager


class SymbolResolver:
    def __init__(self, capital: float) -> None:
        self.capital = capital

    def resolve(self) -> list[dict]:
        manager = CapitalManager(self.capital)

        return manager.allowed_symbols()
