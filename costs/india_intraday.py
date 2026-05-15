"""India intraday cost model for the backtest and daily audit.

Every rate carries a source comment. Indian regulators and exchanges revise
these regularly (the Budget 2026 STT hike landed in April 2026); if a rate
changes, update here AND re-verify against the cited source. Do not
silently bump a number — the comment is the audit trail.

A single `IndiaIntradayCostModel.round_trip_cost(...)` call computes the
full basket — brokerage, STT, exchange charges, GST, SEBI fees, stamp duty —
for one BUY + one SELL leg. The caller subtracts `total` from gross PnL.

Segments supported in Pass A:
    EQUITY_INDEX_FUT   NIFTY / BANKNIFTY index futures
    EQUITY_INDEX_OPT   NIFTY / BANKNIFTY options (premium-based STT)
    MCX_COMM_FUT       MCX gold / crude / non-agri commodity futures
"""

from dataclasses import asdict, dataclass
from typing import Literal, Optional

InstrumentClass = Literal[
    "EQUITY_INDEX_FUT",
    "EQUITY_INDEX_OPT",
    "MCX_COMM_FUT",
]


# ----------------------------------------------------------- rate constants
#
# All rates are PERCENT (so 0.05 means 0.05%, not 0.5%). Sources cited
# inline; current as of November 2025 / post-Budget-2026 effective rates.

# STT — sell side only.
#   Source: Finance (No.2) Act 2026, effective 1 April 2026. Cross-refs:
#     - https://cleartax.in/s/securities-transaction-tax-stt
#     - https://www.nseindia.com/static/products-services/equity-derivatives-securities-transaction-tax
#   Pre-April-2026 rates were FUT 0.02% / OPT 0.0625%; update here AND
#   the comment if the law changes again.
STT_FUTURES_SELL_PCT = 0.05      # NSE futures: 0.05% of sell-side turnover
STT_OPTIONS_SELL_PCT = 0.15      # NSE options: 0.15% of sell-side premium
STT_MCX_FUT_SELL_PCT = 0.01      # MCX non-agri: 0.01% of sell-side turnover

# Exchange transaction charges. Round-trip turnover.
#   Sources: NSE circular FA56129 (NSE F&O) and Zerodha charge sheet
#     https://zerodha.com/charges/ (cross-checked Nov 2025).
NSE_FNO_FUT_EXCHANGE_PCT = 0.0019    # NSE FUTIDX
NSE_FNO_OPT_EXCHANGE_PCT = 0.05      # NSE OPTIDX on premium
MCX_NONAGRI_EXCHANGE_PCT = 0.0026    # MCX gold/crude: ₹260 / crore

# SEBI turnover fee — ₹10 per crore of turnover.
#   Source: SEBI fee notification; published at
#     https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies
SEBI_FEE_PER_CRORE = 10.0

# Stamp duty — BUY side only.
#   Source: Finance Act 2019 uniform stamp duty schedule (states harmonised).
#     Futures: 0.002% of buy-side turnover
#     Options: 0.003% of buy-side premium
STAMP_DUTY_FUT_BUY_PCT = 0.002
STAMP_DUTY_OPT_BUY_PCT = 0.003

# GST 18% on (brokerage + exchange + SEBI). NOT applied to STT or stamp duty.
#   Source: https://support.zerodha.com/category/.../how-is-the-securities-transaction-tax-stt-calculated
GST_PCT = 18.0

# Discount-broker flat brokerage. Most Indian discount brokers (Zerodha,
# Upstox, Dhan) cap intraday F&O brokerage at ₹20 per executed order; this
# is the default we use. Operators with negotiated rates should override
# via `CostRates(brokerage_per_order=...)`.
BROKERAGE_PER_ORDER_INR = 20.0


@dataclass
class CostRates:
    """All rates as percentages."""
    brokerage_per_order: float = BROKERAGE_PER_ORDER_INR
    stt_futures_sell_pct: float = STT_FUTURES_SELL_PCT
    stt_options_sell_pct: float = STT_OPTIONS_SELL_PCT
    stt_mcx_fut_sell_pct: float = STT_MCX_FUT_SELL_PCT
    nse_fno_fut_exchange_pct: float = NSE_FNO_FUT_EXCHANGE_PCT
    nse_fno_opt_exchange_pct: float = NSE_FNO_OPT_EXCHANGE_PCT
    mcx_nonagri_exchange_pct: float = MCX_NONAGRI_EXCHANGE_PCT
    sebi_fee_per_crore: float = SEBI_FEE_PER_CRORE
    stamp_duty_fut_buy_pct: float = STAMP_DUTY_FUT_BUY_PCT
    stamp_duty_opt_buy_pct: float = STAMP_DUTY_OPT_BUY_PCT
    gst_pct: float = GST_PCT


@dataclass
class TradeCosts:
    brokerage: float
    stt: float
    exchange_charges: float
    sebi_fees: float
    stamp_duty: float
    gst: float
    total: float

    def as_dict(self) -> dict:
        return {k: round(v, 2) for k, v in asdict(self).items()}


class IndiaIntradayCostModel:
    def __init__(self, rates: Optional[CostRates] = None) -> None:
        self.rates = rates or CostRates()

    def round_trip_cost(
        self,
        segment: str,
        side: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        instrument_class: Optional[InstrumentClass] = None,
    ) -> TradeCosts:
        """Cost for one BUY + one SELL leg (`quantity` each).

        `segment` and `side` come from the strategy layer for symmetry
        with `BrokerAdapter` semantics, but cost computation depends
        only on the instrument class (FUT/OPT/MCX). `side="LONG"` means
        buy-then-sell (entry_price is BUY); `side="SHORT"` is sell-then-
        buy (entry_price is SELL). Either way, STT only applies to the
        sell leg's turnover and stamp duty only to the buy leg.

        If `instrument_class` is omitted, derive from `segment`:
            NSE_FNO  -> EQUITY_INDEX_FUT (we don't have OPT trades here)
            MCX_COMM -> MCX_COMM_FUT
        """
        if quantity <= 0:
            return TradeCosts(0, 0, 0, 0, 0, 0, 0)

        if instrument_class is None:
            instrument_class = _derive_class(segment)

        # Direction-aware turnover. For LONG: entry is BUY, exit is SELL.
        # For SHORT: entry is SELL, exit is BUY.
        if side.upper() == "LONG":
            buy_turnover = entry_price * quantity
            sell_turnover = exit_price * quantity
        else:
            buy_turnover = exit_price * quantity
            sell_turnover = entry_price * quantity
        round_trip_turnover = buy_turnover + sell_turnover

        rates = self.rates
        brokerage = 2 * rates.brokerage_per_order

        # STT — sell side only.
        if instrument_class == "EQUITY_INDEX_FUT":
            stt = sell_turnover * (rates.stt_futures_sell_pct / 100.0)
        elif instrument_class == "EQUITY_INDEX_OPT":
            stt = sell_turnover * (rates.stt_options_sell_pct / 100.0)
        elif instrument_class == "MCX_COMM_FUT":
            stt = sell_turnover * (rates.stt_mcx_fut_sell_pct / 100.0)
        else:
            stt = 0.0

        # Exchange transaction charges — round-trip turnover.
        if instrument_class == "EQUITY_INDEX_FUT":
            exchange_charges = round_trip_turnover * (rates.nse_fno_fut_exchange_pct / 100.0)
        elif instrument_class == "EQUITY_INDEX_OPT":
            exchange_charges = round_trip_turnover * (rates.nse_fno_opt_exchange_pct / 100.0)
        elif instrument_class == "MCX_COMM_FUT":
            exchange_charges = round_trip_turnover * (rates.mcx_nonagri_exchange_pct / 100.0)
        else:
            exchange_charges = 0.0

        # SEBI turnover fee.
        sebi_fees = (round_trip_turnover / 1_00_00_000.0) * rates.sebi_fee_per_crore

        # Stamp duty — BUY side only.
        if instrument_class == "EQUITY_INDEX_OPT":
            stamp_duty = buy_turnover * (rates.stamp_duty_opt_buy_pct / 100.0)
        else:
            stamp_duty = buy_turnover * (rates.stamp_duty_fut_buy_pct / 100.0)

        # GST on (brokerage + exchange + SEBI).
        gst = (brokerage + exchange_charges + sebi_fees) * (rates.gst_pct / 100.0)

        total = brokerage + stt + exchange_charges + sebi_fees + stamp_duty + gst
        return TradeCosts(
            brokerage=brokerage,
            stt=stt,
            exchange_charges=exchange_charges,
            sebi_fees=sebi_fees,
            stamp_duty=stamp_duty,
            gst=gst,
            total=total,
        )


def _derive_class(segment: str) -> InstrumentClass:
    seg = (segment or "").upper()
    if seg == "MCX_COMM":
        return "MCX_COMM_FUT"
    return "EQUITY_INDEX_FUT"
