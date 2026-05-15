"""India intraday cost model.

Calibrated against the rate sheet documented in `costs/india_intraday.py`.
The numbers below are not magic — they fall out of the formulae for a
known fixture and pin the line items so a rate change can't silently
inflate backtest PnL.
"""

import pytest

from costs.india_intraday import CostBreakdown, CostRates, round_trip_cost


def test_round_trip_zero_qty_is_zero():
    c = round_trip_cost(100, 101, 0, "EQUITY_INDEX_FUT")
    assert c.total == 0.0


def test_equity_index_fut_breakdown_components_nonzero():
    """Buy at 100, sell at 101, qty 50. Each line item should compute
    cleanly. We assert the *sum decomposition* rather than exact rupees
    so the test survives small rate tweaks but catches a missing line.
    """
    c = round_trip_cost(
        entry_price=100, exit_price=101, quantity=50,
        instrument_class="EQUITY_INDEX_FUT",
    )
    assert c.brokerage > 0  # 2 x 20 = 40
    assert c.stt > 0
    assert c.exchange_charge > 0
    assert c.sebi_fee >= 0
    assert c.stamp_duty > 0
    assert c.gst > 0
    # All components add to total.
    summed = (c.brokerage + c.stt + c.exchange_charge + c.sebi_fee
              + c.stamp_duty + c.gst)
    assert c.total == pytest.approx(summed, rel=1e-9)


def test_stt_only_on_sell_side_for_futures():
    """STT on a sell turnover of 101*50 = 5050 at 0.0125% => 0.63125 INR.

    Verifies the formula directly so a future formula change has to
    re-derive this constant.
    """
    c = round_trip_cost(100, 101, 50, "EQUITY_INDEX_FUT")
    assert c.stt == pytest.approx(101 * 50 * (0.0125 / 100), rel=1e-9)


def test_options_stt_higher_than_futures():
    """Options sell-side STT is 0.0625% (5x futures). Same trade size
    must therefore have a higher STT line."""
    c_fut = round_trip_cost(100, 101, 50, "EQUITY_INDEX_FUT")
    c_opt = round_trip_cost(100, 101, 50, "EQUITY_INDEX_OPT")
    assert c_opt.stt == pytest.approx(c_fut.stt * 5, rel=1e-9)


def test_mcx_uses_mcx_stamp_duty_rate():
    """MCX stamp duty is 0.003% vs 0.002% for FNO."""
    c_fno = round_trip_cost(100, 101, 50, "EQUITY_INDEX_FUT")
    c_mcx = round_trip_cost(100, 101, 50, "MCX_COMM_FUT")
    # Stamp duty is on BUY turnover only (entry side).
    assert c_mcx.stamp_duty == pytest.approx(c_fno.stamp_duty * 1.5, rel=1e-9)


def test_custom_rates_propagated():
    rates = CostRates(brokerage_per_order=0.0)  # zero-brokerage broker
    c = round_trip_cost(100, 101, 50, "EQUITY_INDEX_FUT", rates=rates)
    assert c.brokerage == 0.0
    # GST only on (brokerage + exchange + sebi), so zero brokerage cuts
    # the GST too. It should still be > 0 due to exchange/sebi.
    assert c.gst > 0


def test_as_dict_rounding_to_2dp():
    c = round_trip_cost(123.45, 124.67, 25, "EQUITY_INDEX_FUT")
    d = c.as_dict()
    for k, v in d.items():
        assert isinstance(v, (int, float))
        # Round-trip should be 2 decimal places.
        assert v == round(v, 2)
