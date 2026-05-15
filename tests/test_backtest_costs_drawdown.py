"""BacktestEngine output includes gross/net PnL, equity curve, drawdown.

Without a `cost_model`, net_pnl == gross_pnl and total_costs == 0.
With one attached, net_pnl < gross_pnl and per-trade cost breakdowns are
present.
"""

from datetime import time
from pathlib import Path

import pandas as pd
import pytest

from backtest.backtest_engine import BacktestEngine
from costs.india_intraday import IndiaIntradayCostModel
from engine.strategy_runner import StrategyConfig


def _config() -> StrategyConfig:
    return StrategyConfig(
        trigger_dist=20.0, tp_dist=30.0, sl_dist=40.0,
        lock_step=5.0, lock_steps_count=6,
        anchor_time=time(9, 15), square_off_time=time(15, 15),
        tick_size=0.05,
    )


def _three_session_fixture(tmp_path: Path) -> Path:
    rows = [
        # Day 1: long winner (anchor 100 → entry 120 → TP 150)
        {"time": "2026-01-05 09:15", "open": 100, "high": 100, "low": 100, "close": 100},
        {"time": "2026-01-05 09:20", "open": 100, "high": 121, "low": 99, "close": 121},
        {"time": "2026-01-05 09:25", "open": 121, "high": 155, "low": 120, "close": 152},
        # Day 2: short loser (anchor 200 → short entry 180 → SL 220)
        {"time": "2026-01-06 09:15", "open": 200, "high": 200, "low": 200, "close": 200},
        {"time": "2026-01-06 09:20", "open": 200, "high": 199, "low": 179, "close": 181},
        {"time": "2026-01-06 09:25", "open": 181, "high": 221, "low": 178, "close": 219},
        # Day 3: another long winner
        {"time": "2026-01-07 09:15", "open": 300, "high": 300, "low": 300, "close": 300},
        {"time": "2026-01-07 09:20", "open": 300, "high": 322, "low": 299, "close": 321},
        {"time": "2026-01-07 09:25", "open": 321, "high": 355, "low": 320, "close": 352},
    ]
    csv = tmp_path / "fx.csv"
    pd.DataFrame(rows).to_csv(csv, index=False)
    return csv


def test_no_cost_model_net_equals_gross(tmp_path):
    csv = _three_session_fixture(tmp_path)
    engine = BacktestEngine(_config())
    result = engine.run(csv_path=str(csv), quantity=50, money_per_point=1.0)
    assert result["total_costs"] == 0
    assert result["total_net_pnl"] == result["total_gross_pnl"]
    assert result["cost_model_attached"] is False


def test_cost_model_attached_lowers_net_pnl(tmp_path):
    csv = _three_session_fixture(tmp_path)
    nifty_entry = {
        "symbol": "NIFTY",
        "segment": "NSE_FNO",
        "instrument_class": "EQUITY_INDEX_FUT",
    }
    engine = BacktestEngine(
        _config(),
        cost_model=IndiaIntradayCostModel(),
        symbol_entry=nifty_entry,
    )
    result = engine.run(csv_path=str(csv), quantity=50, money_per_point=1.0)
    assert result["total_costs"] > 0
    assert result["total_net_pnl"] < result["total_gross_pnl"]
    assert result["cost_model_attached"] is True
    # cost line equals gross - net.
    assert result["total_gross_pnl"] - result["total_net_pnl"] == pytest.approx(
        result["total_costs"], abs=0.01
    )


def test_per_trade_costs_in_trade_results(tmp_path):
    csv = _three_session_fixture(tmp_path)
    engine = BacktestEngine(
        _config(),
        cost_model=IndiaIntradayCostModel(),
        symbol_entry={"segment": "NSE_FNO", "instrument_class": "EQUITY_INDEX_FUT"},
    )
    result = engine.run(csv_path=str(csv), quantity=50, money_per_point=1.0)
    for t in result["trade_results"]:
        assert t["costs"] is not None
        assert "total" in t["costs"]
        assert "net_money_pnl" in t


def test_equity_curve_length_matches_trade_count(tmp_path):
    csv = _three_session_fixture(tmp_path)
    engine = BacktestEngine(
        _config(),
        cost_model=IndiaIntradayCostModel(),
        symbol_entry={"segment": "NSE_FNO", "instrument_class": "EQUITY_INDEX_FUT"},
    )
    result = engine.run(csv_path=str(csv), quantity=50, money_per_point=1.0)
    assert len(result["equity_curve"]) == result["trades"]
    # Each entry is [ts_iso, cumulative_net].
    for ts, cum in result["equity_curve"]:
        assert isinstance(ts, str)
        assert isinstance(cum, (int, float))


def test_max_drawdown_nonnegative(tmp_path):
    csv = _three_session_fixture(tmp_path)
    engine = BacktestEngine(_config())
    result = engine.run(csv_path=str(csv), quantity=50, money_per_point=1.0)
    assert result["max_drawdown_money"] >= 0
    assert result["max_drawdown_pct"] >= 0
