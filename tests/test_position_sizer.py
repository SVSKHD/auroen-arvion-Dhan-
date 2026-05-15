"""Position sizing — class-based PositionSizer.

The Pass A brief specifies:

    raw_lots = (capital × max_risk_pct/100) / (sl_dist × money_per_point × lot_size)
    qty      = floor(raw_lots) × lot_size

The first test case from the brief is verified directly. The brief's
second case ("capital=500_000 same params → 50 (1 lot)") doesn't satisfy
the stated formula — `500_000 * 0.01 / (40 * 50 * 50) = 0.05`, which
floors to 0. So we test the principle (raise capital until 1 lot fits)
with the smallest capital that legitimately yields one lot.
"""

import pytest

from risk.position_sizer import PositionSizer


def test_brief_example_under_one_lot_refuses():
    """capital=25_000, max_risk_pct=1, sl_dist=40, money_per_point=50,
    lot_size=50. Per-lot risk = 40*50*50 = 100_000. Risk budget = 250.
    floor(250 / 100_000) = 0 → REFUSE."""
    sizer = PositionSizer(capital=25_000, max_risk_pct=1)
    decision = sizer.size(sl_dist=40, money_per_point=50, lot_size=50)
    assert decision.quantity == 0
    assert decision.lots == 0
    assert decision.skipped is True
    assert decision.skipped_reason == "RISK_BELOW_ONE_LOT"
    assert decision.per_lot_risk == 100_000.0


def test_just_enough_capital_for_one_lot():
    """Smallest capital that satisfies one lot of NIFTY at the brief's
    other params. per-lot risk = 100_000, max_risk_pct = 1, so we need
    capital = 100_000 / 0.01 = 10_000_000 to fit exactly one lot."""
    sizer = PositionSizer(capital=10_000_000, max_risk_pct=1)
    decision = sizer.size(sl_dist=40, money_per_point=50, lot_size=50)
    assert decision.lots == 1
    assert decision.quantity == 50
    assert decision.skipped is False


def test_more_capital_more_lots_floored():
    """capital=15_000_000 @1%: risk_budget=150_000, per_lot_risk=100_000.
    floor(1.5) = 1 lot. NSE F&O reject non-lot multiples so we floor."""
    sizer = PositionSizer(capital=15_000_000, max_risk_pct=1)
    decision = sizer.size(sl_dist=40, money_per_point=50, lot_size=50)
    assert decision.lots == 1
    assert decision.quantity == 50


def test_money_per_point_realistic_for_nifty():
    """With money_per_point=1 (per-CONTRACT INR per point, the convention
    used by config/strategy_config.py SUPPORTED_SYMBOLS), per-lot risk on
    NIFTY at SL=40 is 40*1*50 = 2000. capital=500_000 @1% → 2.5 lots →
    floor 2 → qty=100. This is the realistic Pass A integration value."""
    sizer = PositionSizer(capital=500_000, max_risk_pct=1)
    decision = sizer.size(sl_dist=40, money_per_point=1, lot_size=50)
    assert decision.lots == 2
    assert decision.quantity == 100


def test_zero_capital_skips():
    decision = PositionSizer(0, 1).size(40, 1, 50)
    assert decision.skipped
    assert decision.skipped_reason == "ZERO_RISK_BUDGET"


def test_zero_max_risk_skips():
    decision = PositionSizer(1_000_000, 0).size(40, 1, 50)
    assert decision.skipped
    assert decision.skipped_reason == "ZERO_RISK_BUDGET"


def test_bad_instrument_params_skip():
    sizer = PositionSizer(1_000_000, 1)
    assert sizer.size(0, 1, 50).skipped
    assert sizer.size(40, 0, 50).skipped
    assert sizer.size(40, 1, 0).skipped


def test_integer_lot_flooring_at_boundary():
    """raw_lots is exactly between two integers: must floor, not round."""
    # per-lot risk 2000, budget needs to give raw_lots ≈ 1.999 -> 1 lot
    # budget = 3998 -> capital * 0.01 = 3998 -> capital = 399_800
    sizer = PositionSizer(capital=399_800, max_risk_pct=1)
    decision = sizer.size(sl_dist=40, money_per_point=1, lot_size=50)
    assert decision.lots == 1
    # Bump capital so raw_lots crosses 2 cleanly: capital = 400_001
    sizer2 = PositionSizer(capital=400_001, max_risk_pct=1)
    d2 = sizer2.size(sl_dist=40, money_per_point=1, lot_size=50)
    assert d2.lots == 2
    assert d2.quantity == 100


def test_skipped_decision_includes_diagnostic_fields():
    """When a trade is refused we need to be able to alert with the
    numbers — capital, sl_dist, etc. — not just a boolean."""
    sizer = PositionSizer(capital=25_000, max_risk_pct=1)
    d = sizer.size(sl_dist=40, money_per_point=50, lot_size=50)
    assert d.risk_budget == 250.0
    assert d.per_lot_risk == 100_000.0
    assert d.raw_lots == pytest.approx(0.0025)
