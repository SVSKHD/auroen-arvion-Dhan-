from datetime import datetime


class AnchorEngine:
    def __init__(self) -> None:
        self.anchor_prices: dict[str, float] = {}

    def set_anchor(self, symbol: str, price: float) -> None:
        self.anchor_prices[symbol] = price

    def get_anchor(self, symbol: str) -> float | None:
        return self.anchor_prices.get(symbol)

    def should_capture_anchor(self) -> bool:
        now = datetime.now()

        return now.hour == 9 and now.minute == 15
