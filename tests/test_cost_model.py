"""India intraday cost model — hand-computed fixtures.

Each test pins a single line item or the total against numbers derived
from the rate constants in `costs/india_intraday.py`. If a regulator
changes a rate (Budget revisions, NSE circulars), update the source
comment in india_intraday.py first, then update these expected values
with a derivation in the test docstring.
"""

import pytest

from costs.india_intraday import IndiaIntradayCostModel, CostRates, TradeCosts


def test_zero_quantity_zero_cost():
    cm = IndiaIntradayCostModel()
    tc = cm.round_trip_cost("NSE_FNO", "LONG", 19800, 19830, 0)
    assert tc.total == 0


# ----------- NIFTY index futures, 1 lot (50 contracts), LONG ---------------


def test_nifty_futures_round_trip_components():
    """Entry 19800 → exit 19830, qty 50, NIFTY EQUITY_INDEX_FUT.

    buy_turnover    = 19800 * 50 = 990_000
    sell_turnover   = 19830 * 50 = 991_500
    round_trip      = 1_981_500

    brokerage       = 2 * 20 = 40
    STT  (0.05% of sell)         = 991_500 * 0.0005 = 495.75
    exch (0.0019% of round-trip) = 1_981_500 * 0.000019 = 37.6485
    SEBI (₹10 / crore RT)        = 1_981_500 / 1e7 * 10 = 1.9815
    stamp(0.002% of buy)         = 990_000 * 0.00002 = 19.8
    GST  (18% of brokerage+exch+sebi) = (40 + 37.6485 + 1.9815) * 0.18 = 14.331354
    total           = 40 + 495.75 + 37.6485 + 1.9815 + 19.8 + 14.331354 ≈ 609.51
    """
    cm = IndiaIntradayCostModel()
    tc = cm.round_trip_cost("NSE_FNO", "LONG", 19800.0, 19830.0, 50,
                            instrument_class="EQUITY_INDEX_FUT")

    assert tc.brokerage == pytest.approx(40.0)
    assert tc.stt == pytest.approx(991500 * 0.0005)
    assert tc.exchange_charges == pytest.approx(1981500 * 0.000019)
    assert tc.sebi_fees == pytest.approx(1981500 / 1e7 * 10)
    assert tc.stamp_duty == pytest.approx(990000 * 0.00002)
    expected_gst = (40 + tc.exchange_charges + tc.sebi_fees) * 0.18
    assert tc.gst == pytest.approx(expected_gst)
    expected_total = (tc.brokerage + tc.stt + tc.exchange_charges
                      + tc.sebi_fees + tc.stamp_duty + tc.gst)
    assert tc.total == pytest.approx(expected_total)


def test_short_direction_swaps_buy_and_sell_turnover():
    """SHORT entry=19830 → exit=19800. The SELL happens at 19830, BUY
    at 19800 (when we cover). So stamp duty (BUY-only) is on 19800*50,
    and STT (SELL-only) is on 19830*50 — opposite of LONG."""
    cm = IndiaIntradayCostModel()
    long_tc = cm.round_trip_cost("NSE_FNO", "LONG", 19800, 19830, 50,
                                 instrument_class="EQUITY_INDEX_FUT")
    short_tc = cm.round_trip_cost("NSE_FNO", "SHORT", 19830, 19800, 50,
                                  instrument_class="EQUITY_INDEX_FUT")
    # For SHORT entry=19830 (sell), exit=19800 (buy).
    assert short_tc.stt == pytest.approx(19830 * 50 * 0.0005)
    assert short_tc.stamp_duty == pytest.approx(19800 * 50 * 0.00002)
    # Brokerage and exchange (round-trip) are direction-agnostic.
    assert short_tc.brokerage == long_tc.brokerage
    assert short_tc.exchange_charges == long_tc.exchange_charges


# ----------- BANKNIFTY index futures, 1 lot (15 contracts) ----------------


def test_banknifty_futures_total_against_derivation():
    """Entry 50000 → exit 50250, qty 15, BANKNIFTY.

    buy_t   = 50000 * 15 = 750_000
    sell_t  = 50250 * 15 = 753_750
    rt      = 1_503_750

    brokerage = 40
    stt       = 753750 * 0.0005 = 376.875
    exch      = 1503750 * 0.000019 = 28.57125
    sebi      = 1503750/1e7*10 = 1.50375
    stamp     = 750000 * 0.00002 = 15.0
    gst       = (40 + 28.57125 + 1.50375) * 0.18 = 12.6135
    total     = 40 + 376.875 + 28.57125 + 1.50375 + 15.0 + 12.6135 ≈ 474.56
    """
    cm = IndiaIntradayCostModel()
    tc = cm.round_trip_cost("NSE_FNO", "LONG", 50000.0, 50250.0, 15,
                            instrument_class="EQUITY_INDEX_FUT")
    assert tc.total == pytest.approx(474.56, abs=0.01)


# ----------- MCX gold (non-agri commodity) -------------------------------


def test_mcx_commodity_round_trip_uses_mcx_rates():
    """Entry 70_000 → exit 70_300, qty 100, MCX_GOLD.

    Different STT rate (0.01% vs 0.05% for equity futures) and different
    exchange charge rate (0.0026% vs 0.0019%). Stamp duty unchanged
    (0.002% BUY).
    """
    cm = IndiaIntradayCostModel()
    tc = cm.round_trip_cost("MCX_COMM", "LONG", 70000.0, 70300.0, 100,
                            instrument_class="MCX_COMM_FUT")
    # STT on sell side: 70300 * 100 * 0.0001 = 703.0
    assert tc.stt == pytest.approx(703.0)
    # Exchange: round-trip turnover * 0.000026
    rt = 70000 * 100 + 70300 * 100
    assert tc.exchange_charges == pytest.approx(rt * 0.000026)
    # Stamp duty: BUY * 0.00002
    assert tc.stamp_duty == pytest.approx(70000 * 100 * 0.00002)


# ----------- segment-only derivation when instrument_class omitted -------


def test_segment_only_derives_instrument_class():
    cm = IndiaIntradayCostModel()
    nfo = cm.round_trip_cost("NSE_FNO", "LONG", 19800, 19830, 50)
    mcx = cm.round_trip_cost("MCX_COMM", "LONG", 70000, 70300, 100)
    # NSE_FNO -> futures STT rate; MCX_COMM -> MCX STT rate.
    assert nfo.stt > 0
    assert mcx.stt > 0
    # Different STT rates produce visibly different STT per same turnover.
    nfo_per_unit = nfo.stt / (19830 * 50)
    mcx_per_unit = mcx.stt / (70300 * 100)
    assert nfo_per_unit > mcx_per_unit  # 0.05% > 0.01%


# ----------- custom rates -------------------------------------------------


def test_custom_rates_propagate():
    """Operators with negotiated broker tiers should be able to override."""
    rates = CostRates(brokerage_per_order=0.0)
    cm = IndiaIntradayCostModel(rates=rates)
    tc = cm.round_trip_cost("NSE_FNO", "LONG", 19800, 19830, 50)
    assert tc.brokerage == 0
    # GST falls when brokerage falls — only on (brokerage + exch + sebi).
    assert tc.gst < (50.0 * 0.18)  # baseline GST would be larger


def test_as_dict_is_2dp_rounded():
    cm = IndiaIntradayCostModel()
    tc = cm.round_trip_cost("NSE_FNO", "LONG", 19800.123, 19830.456, 50)
    d = tc.as_dict()
    for k, v in d.items():
        assert isinstance(v, (int, float))
        assert v == round(v, 2)
