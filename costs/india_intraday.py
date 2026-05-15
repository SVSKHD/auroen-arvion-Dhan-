"""India intraday cost model for backtest realism.

Per-round-trip costs for NSE F&O (futures + options) and MCX commodities.
Rates are configurable; defaults below reflect publicly published schedules
as of 2026Q1. Operators should tune these against their actual broker tier
and re-run backtests — the cost-curve is meaningful enough that a strategy
that looks profitable on gross PnL can be net-loss after costs.

For each round-trip we compute:

  - Brokerage: flat per-order (most discount brokers cap at INR 20).
  - STT (Securities Transaction Tax): on the SELL side only for futures
    and options. Different rates for futures vs options vs equity.
  - Exchange transaction charge: percent of turnover, NSE vs MCX differ.
  - SEBI fee: tiny flat per-crore turnover.
  - Stamp duty: BUY side, percent of turnover.
  - GST: 18% on (brokerage + exchange charges + SEBI).

Output `CostBreakdown` reports each line item so the backtest summary can
surface the leakage transparently. Backtest applies one round-trip per
trade (entry + exit count as 2 orders) and subtracts the total from gross
PnL to produce net PnL.

References used to calibrate the defaults:
  - NSE F&O fee circular, 2026Q1
  - MCX brokerage tariff, 2026Q1
  - Discount-broker public price list (Zerodha/Upstox/Dhan)

If Dhan publishes a different rate, override the relevant field in
`CostRates` and re-run.
"""

from dataclasses import dataclass, field
from typing import Literal

InstrumentClass = Literal["EQUITY_INDEX_FUT", "EQUITY_INDEX_OPT", "MCX_COMM_FUT"]


@dataclass
class CostRates:
    brokerage_per_order: float = 20.0  # INR flat, capped at 20 by most discount brokers
    stt_futures_sell_pct: float = 0.0125  # 0.0125% of sell-side turnover
    stt_options_sell_pct: float = 0.0625  # 0.0625% of options sell-side premium
    exchange_charge_nse_fno_pct: float = 0.0019  # 0.0019%
    exchange_charge_mcx_pct: float = 0.0026  # 0.0026%
    sebi_fee_per_crore: float = 10.0  # INR per crore of turnover
    stamp_duty_buy_pct: float = 0.002  # 0.002% on BUY side for FNO; 0.003% for MCX
    stamp_duty_buy_pct_mcx: float = 0.003
    gst_pct: float = 18.0  # GST on brokerage + exchange + SEBI


@dataclass
class CostBreakdown:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_charge: float = 0.0
    sebi_fee: float = 0.0
    stamp_duty: float = 0.0
    gst: float = 0.0
    total: float = 0.0

    def as_dict(self) -> dict:
        return {
            "brokerage": round(self.brokerage, 2),
            "stt": round(self.stt, 2),
            "exchange_charge": round(self.exchange_charge, 2),
            "sebi_fee": round(self.sebi_fee, 2),
            "stamp_duty": round(self.stamp_duty, 2),
            "gst": round(self.gst, 2),
            "total": round(self.total, 2),
        }


def round_trip_cost(
    entry_price: float,
    exit_price: float,
    quantity: int,
    instrument_class: InstrumentClass,
    rates: CostRates = None,
) -> CostBreakdown:
    """Compute total cost for one entry + one exit (a "round trip").

    Side conventions: BUY-side turnover = entry_price × quantity for LONG
    or exit_price × quantity for SHORT. STT is computed against whichever
    side is the SELL; for futures this is half the round-trip turnover at
    the exit price (LONG) or the entry price (SHORT).

    For simplicity we charge STT on the *exit-side turnover* regardless of
    direction — for the breakout strategy, where the exit happens
    after the entry in time, this matches operator-facing brokerage
    statements within rounding.
    """
    rates = rates or CostRates()
    if quantity <= 0:
        return CostBreakdown()

    buy_turnover = entry_price * quantity
    sell_turnover = exit_price * quantity
    round_trip_turnover = buy_turnover + sell_turnover

    # Brokerage: 2 orders.
    brokerage = 2 * rates.brokerage_per_order

    # STT (sell side only).
    if instrument_class == "EQUITY_INDEX_FUT":
        stt = sell_turnover * (rates.stt_futures_sell_pct / 100.0)
    elif instrument_class == "EQUITY_INDEX_OPT":
        stt = sell_turnover * (rates.stt_options_sell_pct / 100.0)
    elif instrument_class == "MCX_COMM_FUT":
        # MCX commodity futures: STT applies but at the same band as
        # equity futures for non-agri commodities.
        stt = sell_turnover * (rates.stt_futures_sell_pct / 100.0)
    else:
        stt = 0.0

    # Exchange transaction charge.
    if instrument_class.startswith("EQUITY"):
        exchange_charge = round_trip_turnover * (rates.exchange_charge_nse_fno_pct / 100.0)
    else:
        exchange_charge = round_trip_turnover * (rates.exchange_charge_mcx_pct / 100.0)

    # SEBI fee.
    sebi_fee = (round_trip_turnover / 1_00_00_000.0) * rates.sebi_fee_per_crore

    # Stamp duty (BUY side only).
    if instrument_class == "MCX_COMM_FUT":
        stamp_duty = buy_turnover * (rates.stamp_duty_buy_pct_mcx / 100.0)
    else:
        stamp_duty = buy_turnover * (rates.stamp_duty_buy_pct / 100.0)

    # GST on brokerage + exchange charge + SEBI.
    gst = (brokerage + exchange_charge + sebi_fee) * (rates.gst_pct / 100.0)

    total = brokerage + stt + exchange_charge + sebi_fee + stamp_duty + gst

    return CostBreakdown(
        brokerage=brokerage,
        stt=stt,
        exchange_charge=exchange_charge,
        sebi_fee=sebi_fee,
        stamp_duty=stamp_duty,
        gst=gst,
        total=total,
    )
