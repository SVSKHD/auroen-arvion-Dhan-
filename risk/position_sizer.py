"""Capital-based position sizing.

Formula (per Phase 6 Pass A brief):

    raw_lots = (capital * max_risk_pct/100) / (sl_dist * money_per_point * lot_size)
    qty      = floor(raw_lots) * lot_size

`money_per_point` is the per-CONTRACT INR move per 1-point price change.
Multiplying by `lot_size` converts to per-LOT INR risk for the `sl_dist`
stop distance. We floor to integer LOTS first, then expand back — NSE F&O
and MCX reject non-lot quantities at order placement.

`size()` returns 0 (with a `skipped_reason`) when even one lot would
exceed the per-trade risk budget. The caller must refuse the trade and
emit a `guardrail_blocked` audit event with reason `position_size_zero`,
not silently scale the position below the risk budget — the budget is
the contract, and "under-trading" defeats the point of fixing a risk
budget in the first place.
"""

from dataclasses import dataclass
from math import floor


@dataclass
class SizingDecision:
    quantity: int
    lots: int
    raw_lots: float
    risk_budget: float
    per_lot_risk: float
    skipped_reason: str = ""

    @property
    def skipped(self) -> bool:
        return self.quantity == 0


class PositionSizer:
    """Bound to a capital figure and a max-risk percentage at construction.

    A single sizer can serve many symbols within a session. Operators
    that fetch capital live from the broker should re-instantiate at the
    start of each trading day so the day's risk budget reflects current
    account balance.
    """

    def __init__(self, capital: float, max_risk_pct: float) -> None:
        self.capital = float(capital)
        self.max_risk_pct = float(max_risk_pct)

    def size(
        self,
        sl_dist: float,
        money_per_point: float,
        lot_size: int,
    ) -> SizingDecision:
        if self.capital <= 0 or self.max_risk_pct <= 0:
            return SizingDecision(0, 0, 0.0, 0.0, 0.0, "ZERO_RISK_BUDGET")
        if sl_dist <= 0 or money_per_point <= 0 or lot_size <= 0:
            return SizingDecision(0, 0, 0.0, 0.0, 0.0, "BAD_INSTRUMENT_PARAMS")

        risk_budget = self.capital * (self.max_risk_pct / 100.0)
        per_lot_risk = sl_dist * money_per_point * lot_size
        raw_lots = risk_budget / per_lot_risk
        lots = floor(raw_lots)

        if lots <= 0:
            return SizingDecision(
                quantity=0,
                lots=0,
                raw_lots=raw_lots,
                risk_budget=round(risk_budget, 2),
                per_lot_risk=round(per_lot_risk, 2),
                skipped_reason="RISK_BELOW_ONE_LOT",
            )

        return SizingDecision(
            quantity=lots * lot_size,
            lots=lots,
            raw_lots=raw_lots,
            risk_budget=round(risk_budget, 2),
            per_lot_risk=round(per_lot_risk, 2),
        )
