from dataclasses import dataclass


@dataclass
class RiskDecision:
    allowed: bool
    reason: str


class GuardRails:
    def __init__(
        self,
        max_daily_loss: float,
        max_trades_per_day: int,
    ) -> None:
        self.max_daily_loss = max_daily_loss
        self.max_trades_per_day = max_trades_per_day

    def validate_daily_loss(self, current_loss: float) -> RiskDecision:
        if current_loss <= -abs(self.max_daily_loss):
            return RiskDecision(False, "MAX_DAILY_LOSS_HIT")

        return RiskDecision(True, "OK")

    def validate_trade_count(self, current_trades: int) -> RiskDecision:
        if current_trades >= self.max_trades_per_day:
            return RiskDecision(False, "MAX_TRADES_REACHED")

        return RiskDecision(True, "OK")
