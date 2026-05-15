"""Audit reports gross_pnl != net_pnl when a cost model is attached.

Pass A's audit accepts an `IndiaIntradayCostModel` at construction. Each
recorded trade is costed; `daily_summary.json` reports gross_pnl,
net_pnl, total_costs, and a per-trade `costs` line item.

Without a cost model attached, net_pnl == gross_pnl (the legacy Phase 5
behaviour) and the construction logs a one-shot warning.
"""

import json
from pathlib import Path

import pytest

from costs.india_intraday import IndiaIntradayCostModel
from engine.audit import DailyAudit, TradeSnapshot


def _audit(tmp_path: Path, attach_costs: bool) -> DailyAudit:
    return DailyAudit(
        date_iso="2026-01-05",
        root=tmp_path / "audit",
        cost_model=IndiaIntradayCostModel() if attach_costs else None,
    )


def _nifty_long_winner(quantity: int = 50) -> TradeSnapshot:
    return TradeSnapshot(
        symbol="NIFTY",
        side="LONG",
        entry_price=19800.0,
        exit_price=19830.0,
        points_pnl=30.0,
        money_pnl=30.0 * quantity,  # money_per_point = 1 for tests
        exit_reason="TP",
        quantity=quantity,
        segment="NSE_FNO",
        instrument_class="EQUITY_INDEX_FUT",
    )


def test_no_cost_model_means_net_equals_gross(tmp_path):
    audit = _audit(tmp_path, attach_costs=False)
    audit.record_trade(_nifty_long_winner())
    audit.flush()
    summary = json.loads(audit.summary_path.read_text())
    assert summary["gross_pnl"] == summary["net_pnl"]
    assert summary["total_costs"] == 0
    assert summary["cost_model_attached"] is False


def test_cost_model_attached_produces_lower_net_than_gross(tmp_path):
    audit = _audit(tmp_path, attach_costs=True)
    audit.record_trade(_nifty_long_winner())
    audit.flush()
    summary = json.loads(audit.summary_path.read_text())
    assert summary["gross_pnl"] > summary["net_pnl"]
    assert summary["total_costs"] > 0
    assert summary["cost_model_attached"] is True
    # The costs should be the difference, exactly.
    assert summary["gross_pnl"] - summary["net_pnl"] == pytest.approx(summary["total_costs"], abs=0.01)


def test_per_trade_costs_attached_to_each_trade(tmp_path):
    audit = _audit(tmp_path, attach_costs=True)
    audit.record_trade(_nifty_long_winner())
    audit.flush()
    summary = json.loads(audit.summary_path.read_text())
    trade = summary["trades"][0]
    assert trade["costs"] is not None
    assert {"brokerage", "stt", "exchange_charges", "sebi_fees",
            "stamp_duty", "gst", "total"} <= set(trade["costs"].keys())
    assert trade["net_money_pnl"] == round(trade["money_pnl"] - trade["costs"]["total"], 2)


def test_equity_curve_and_drawdown_in_summary(tmp_path):
    audit = _audit(tmp_path, attach_costs=True)
    # +1500 gross net of costs, then -2000 gross net of costs, then +500.
    audit.record_trade(_nifty_long_winner())  # winner
    loser = _nifty_long_winner()
    loser.exit_price = 19760.0  # -40 points, -2000 money
    loser.money_pnl = -2000.0
    loser.points_pnl = -40.0
    loser.exit_reason = "SL"
    audit.record_trade(loser)
    third = _nifty_long_winner()
    third.exit_price = 19810.0  # +10 points, +500 money
    third.money_pnl = 500.0
    third.points_pnl = 10.0
    audit.record_trade(third)
    audit.flush()
    summary = json.loads(audit.summary_path.read_text())
    assert "equity_curve" in summary
    assert len(summary["equity_curve"]) == 3
    assert summary["max_drawdown_money"] > 0  # winner -> loser is the peak-to-trough


def test_zero_quantity_trade_yields_no_costs(tmp_path):
    audit = _audit(tmp_path, attach_costs=True)
    skipped = _nifty_long_winner(quantity=0)
    audit.record_trade(skipped)
    audit.flush()
    summary = json.loads(audit.summary_path.read_text())
    assert summary["total_costs"] == 0
    assert summary["net_pnl"] == summary["gross_pnl"]
