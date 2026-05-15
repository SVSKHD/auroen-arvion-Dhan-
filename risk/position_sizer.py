"""Capital-based position sizing.

Risk per trade = capital × MAX_RISK_PER_TRADE_PCT/100.
Risk per contract = sl_dist × money_per_point.
Quantity = floor(risk_per_trade / risk_per_contract / lot_size) × lot_size.

The floor-to-integer-lots step matters: NSE F&O and MCX both reject orders
that aren't a multiple of the contract's lot size. We compute the number of
*lots* first, then multiply back so the returned quantity is always a clean
multiple.

`size()` returns 0 when the configured risk does not cover even one lot at
the symbol's sl_dist — the caller is expected to skip the trade in that
case rather than under-trade against the configured risk budget.
"""

from dataclasses import dataclass


@dataclass
class SizingDecision:
    quantity: int
    lots: int
    risk_per_trade: float
    risk_per_contract: float
    skipped_reason: str = ""

    @property
    def skipped(self) -> bool:
        return self.quantity == 0


def size(
    capital: float,
    max_risk_per_trade_pct: float,
    sl_dist: float,
    money_per_point: float,
    lot_size: int,
) -> SizingDecision:
    if capital <= 0 or max_risk_per_trade_pct <= 0:
        return SizingDecision(0, 0, 0.0, 0.0, "ZERO_RISK_BUDGET")
    if sl_dist <= 0 or money_per_point <= 0 or lot_size <= 0:
        return SizingDecision(0, 0, 0.0, 0.0, "BAD_INSTRUMENT_PARAMS")

    risk_per_trade = capital * (max_risk_per_trade_pct / 100.0)
    risk_per_contract = sl_dist * money_per_point
    contracts = int(risk_per_trade // risk_per_contract)
    lots = contracts // lot_size

    if lots == 0:
        return SizingDecision(
            quantity=0,
            lots=0,
            risk_per_trade=round(risk_per_trade, 2),
            risk_per_contract=round(risk_per_contract, 2),
            skipped_reason="RISK_BELOW_ONE_LOT",
        )

    return SizingDecision(
        quantity=lots * lot_size,
        lots=lots,
        risk_per_trade=round(risk_per_trade, 2),
        risk_per_contract=round(risk_per_contract, 2),
    )
