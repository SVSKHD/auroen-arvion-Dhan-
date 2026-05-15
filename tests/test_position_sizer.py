"""Capital-based position sizing.

Quantity = floor((capital * risk_pct/100) / (sl_dist * money_per_point) / lot_size) * lot_size.
"""

import pytest

from risk.position_sizer import size


def test_basic_sizing_floors_to_lot_multiple():
    # capital 1,000,000 INR @ 1% risk = 10,000 risk.
    # NIFTY: sl_dist=40, money_per_point=50, lot_size=50.
    # risk_per_contract = 40 * 50 = 2000. contracts = 10000 // 2000 = 5.
    # lots = 5 // 50 = 0. Should be SKIPPED.
    decision = size(
        capital=1_000_000, max_risk_per_trade_pct=1.0,
        sl_dist=40, money_per_point=50, lot_size=50,
    )
    assert decision.quantity == 0
    assert decision.skipped_reason == "RISK_BELOW_ONE_LOT"


def test_sufficient_capital_returns_one_lot():
    # capital 5,000,000 INR @ 1% = 50,000 risk.
    # contracts = 50,000 // 2,000 = 25. lots = 25 // 50 = 0... still 0.
    # Need contracts to exceed lot_size.
    decision = size(
        capital=50_000_000, max_risk_per_trade_pct=1.0,
        sl_dist=40, money_per_point=50, lot_size=50,
    )
    # risk = 500,000; contracts = 250; lots = 5; qty = 250.
    assert decision.lots == 5
    assert decision.quantity == 250
    assert decision.skipped is False


def test_zero_capital_returns_skip():
    decision = size(0, 1.0, 40, 50, 50)
    assert decision.skipped
    assert decision.skipped_reason == "ZERO_RISK_BUDGET"


def test_bad_instrument_params_returns_skip():
    assert size(1_000_000, 1.0, sl_dist=0, money_per_point=50, lot_size=50).skipped
    assert size(1_000_000, 1.0, sl_dist=40, money_per_point=0, lot_size=50).skipped
    assert size(1_000_000, 1.0, sl_dist=40, money_per_point=50, lot_size=0).skipped


def test_risk_per_contract_reported():
    decision = size(50_000_000, 1.0, sl_dist=40, money_per_point=50, lot_size=50)
    assert decision.risk_per_contract == pytest.approx(2000.0)
    assert decision.risk_per_trade == pytest.approx(500_000.0)


def test_smaller_lot_size_more_granular():
    # MCX_GOLD: sl_dist=80, money_per_point=100, lot_size=100.
    # capital 2,000,000 @ 1% = 20,000 risk.
    # risk_per_contract = 80*100 = 8000. contracts = 20,000//8000 = 2.
    # lots = 2 // 100 = 0. Skipped.
    decision = size(2_000_000, 1.0, sl_dist=80, money_per_point=100, lot_size=100)
    assert decision.skipped
