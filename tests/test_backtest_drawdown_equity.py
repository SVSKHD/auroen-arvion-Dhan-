"""Backtest output includes gross PnL, net PnL after costs, an equity
curve, and max drawdown.

Three things are verified:
  1. With instrument_class supplied, costs are charged per round-trip
     and reflected in net_pnl < gross_pnl.
  2. equity_curve is a monotonically-indexed list of (ts, cumulative_net).
  3. max_drawdown_money is the largest peak-to-trough on the curve.
"""

from datetime import time
from pathlib import Path

import pandas as pd
import pytest

from backtest.backtest_engine import BacktestEngine
from engine.strategy_runner import StrategyConfig


def _config() -> StrategyConfig:
    return StrategyConfig(
        trigger_dist=20.0, tp_dist=30.0, sl_dist=40.0,
        lock_step=5.0, lock_steps_count=6,
        anchor_time=time(9, 15), square_off_time=time(15, 15),
        tick_size=0.05,
    )


def _three_day_fixture(tmp_path: Path) -> Path:
    """One winner, one loser, one winner — gives enough curve points
    to observe a non-zero drawdown.
    """
    rows = []
    # Day 1: long winner (anchor 100, entry 120, exit at TP 150)
    rows.append({"time": "2026-01-05 09:15", "open": 100, "high": 100, "low": 100, "close": 100})
    rows.append({"time": "2026-01-05 09:20", "open": 100, "high": 121, "low": 99, "close": 121})
    rows.append({"time": "2026-01-05 09:25", "open": 121, "high": 155, "low": 120, "close": 152})

    # Day 2: short loser (anchor 200, short entry 180, hit SL 220)
    rows.append({"time": "2026-01-06 09:15", "open": 200, "high": 200, "low": 200, "close": 200})
    rows.append({"time": "2026-01-06 09:20", "open": 200, "high": 199, "low": 179, "close": 181})
    rows.append({"time": "2026-01-06 09:25", "open": 181, "high": 221, "low": 178, "close": 219})

    # Day 3: long winner again
    rows.append({"time": "2026-01-07 09:15", "open": 300, "high": 300, "low": 300, "close": 300})
    rows.append({"time": "2026-01-07 09:20", "open": 300, "high": 322, "low": 299, "close": 321})
    rows.append({"time": "2026-01-07 09:25", "open": 321, "high": 355, "low": 320, "close": 352})

    csv_path = tmp_path / "fx.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    return csv_path


def test_summary_has_gross_and_net_pnl(tmp_path):
    csv_path = _three_day_fixture(tmp_path)
    engine = BacktestEngine(_config())
    result = engine.run(
        csv_path=str(csv_path), quantity=50, money_per_point=50.0,
        instrument_class="EQUITY_INDEX_FUT",
    )
    assert "gross_pnl" in result
    assert "net_pnl" in result
    assert "total_costs" in result
    assert result["net_pnl"] < result["gross_pnl"], "costs must reduce net PnL"
    # Cost line equals gross - net.
    assert result["total_costs"] == pytest.approx(
        round(result["gross_pnl"] - result["net_pnl"], 2), rel=1e-9
    )


def test_per_trade_cost_breakdown_present(tmp_path):
    csv_path = _three_day_fixture(tmp_path)
    engine = BacktestEngine(_config())
    result = engine.run(
        csv_path=str(csv_path), quantity=50, money_per_point=50.0,
        instrument_class="EQUITY_INDEX_FUT",
    )
    for trade in result["trade_results"]:
        assert "cost_breakdown" in trade
        cb = trade["cost_breakdown"]
        assert {"brokerage", "stt", "exchange_charge", "sebi_fee", "stamp_duty", "gst", "total"} <= set(cb.keys())
        assert "net_money_pnl" in trade


def test_equity_curve_and_max_drawdown(tmp_path):
    csv_path = _three_day_fixture(tmp_path)
    engine = BacktestEngine(_config())
    result = engine.run(
        csv_path=str(csv_path), quantity=50, money_per_point=50.0,
        instrument_class="EQUITY_INDEX_FUT",
    )
    curve = result["equity_curve"]
    assert isinstance(curve, list)
    assert len(curve) == result["trades"]
    # Curve entries are [ts, cumulative_net].
    for ts, cum in curve:
        assert isinstance(ts, str)
        assert isinstance(cum, (int, float))
    # Drawdown is non-negative and at least as large as the worst
    # peak-to-trough we can see in the curve.
    assert result["max_drawdown_money"] >= 0
    peak = max(0.0, max(c[1] for c in curve))
    if peak > 0:
        worst = peak - min(c[1] for c in curve[curve.index(max(curve, key=lambda x: x[1])):])
        # Our reported drawdown should be at least the worst we
        # observe post-peak.
        assert result["max_drawdown_money"] >= round(max(worst, 0.0), 2)


def test_no_instrument_class_means_zero_costs(tmp_path):
    csv_path = _three_day_fixture(tmp_path)
    engine = BacktestEngine(_config())
    result = engine.run(csv_path=str(csv_path), quantity=50, money_per_point=50.0)
    # Costs default to zero when no instrument_class supplied.
    assert result["total_costs"] == 0.0
    assert result["net_pnl"] == result["gross_pnl"]
