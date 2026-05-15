"""Orchestrator uses PositionSizer when one is attached, and refuses
trades with a `position_size_zero` audit event when the sizer returns 0.
"""

import json
from datetime import datetime, time
from pathlib import Path

import pytest

from brokers.paper.paper_broker import PaperBroker
from core.state_store import StateStore
from core.time_utils import IST
from engine.anchor_engine import AnchorEngine
from engine.audit import DailyAudit
from engine.orchestrator import Orchestrator, _SymbolMeta
from engine.strategy_runner import StrategyConfig
from risk.guardrails import GuardRails
from risk.position_sizer import PositionSizer


def _ist(year, month, day, hour, minute, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=IST)


def _config() -> StrategyConfig:
    return StrategyConfig(
        trigger_dist=20.0, tp_dist=30.0, sl_dist=40.0,
        lock_step=5.0, lock_steps_count=6,
        anchor_time=time(9, 15), square_off_time=time(15, 15),
        tick_size=0.05,
    )


def _store(tmp_path: Path, name: str) -> StateStore:
    s = StateStore.__new__(StateStore)
    s.path = tmp_path / name
    return s


def _read_events(audit: DailyAudit) -> list[dict]:
    if not audit.events_path.exists():
        return []
    return [json.loads(line) for line in audit.events_path.read_text().splitlines() if line.strip()]


def _make(tmp_path, sizer):
    audit_root = tmp_path / "audit"
    audit_root.mkdir()
    audit = DailyAudit(date_iso="2026-01-05", root=audit_root)
    broker = PaperBroker()
    return Orchestrator(
        mode="PAPER",
        broker=broker,
        anchor_engine=AnchorEngine(
            _store(tmp_path, "anchors.json"),
            anchor_time=time(9, 15),
            capture_window_seconds=30,
        ),
        strategy_config=_config(),
        guardrails=GuardRails(max_daily_loss=5000.0, max_trades_per_day=3),
        symbols=[
            _SymbolMeta(
                symbol="NIFTY",
                security_id="49081",
                exchange_segment="NSE_FNO",
                lot_size=50,
                money_per_point=1.0,
                sl_dist=40.0,
            ),
        ],
        state_store=_store(tmp_path, "orchestrator_state.json"),
        audit=audit,
        position_sizer=sizer,
    ), broker, audit


def test_no_sizer_falls_back_to_lot_size(tmp_path):
    """With no PositionSizer attached, the orchestrator preserves the
    Phase 5 behaviour: one lot per OCO leg."""
    orch, broker, _audit = _make(tmp_path, sizer=None)
    orch.on_tick("NIFTY", ltp=19800.0, now=_ist(2026, 1, 5, 9, 15, 5))
    open_orders = broker.get_open_orders()
    assert len(open_orders) == 2
    assert open_orders[0].quantity == 50  # lot_size


def test_sized_orders_match_sizer_output(tmp_path):
    """With per-lot-risk=2000 and risk_budget=5000 (capital 500_000, 1%):
    floor(5000/2000)=2 lots → qty=100 contracts per leg."""
    sizer = PositionSizer(capital=500_000, max_risk_pct=1.0)
    orch, broker, _audit = _make(tmp_path, sizer=sizer)
    orch.on_tick("NIFTY", ltp=19800.0, now=_ist(2026, 1, 5, 9, 15, 5))
    open_orders = broker.get_open_orders()
    assert len(open_orders) == 2
    for o in open_orders:
        assert o.quantity == 100


def test_size_zero_refuses_and_emits_audit_event(tmp_path):
    """Sub-one-lot risk budget: orchestrator must NOT place orders and
    must emit a guardrail_blocked event with reason position_size_zero."""
    sizer = PositionSizer(capital=25_000, max_risk_pct=1.0)
    orch, broker, audit = _make(tmp_path, sizer=sizer)
    orch.on_tick("NIFTY", ltp=19800.0, now=_ist(2026, 1, 5, 9, 15, 5))
    assert broker.get_open_orders() == []
    assert "NIFTY" not in orch.oco_pairs

    blocks = [e for e in _read_events(audit)
              if e["type"] == "guardrail_blocked"
              and e["payload"]["reason"] == "position_size_zero"]
    assert len(blocks) == 1
    payload = blocks[0]["payload"]
    assert payload["capital"] == 25_000
    assert payload["sl_dist"] == 40.0
    assert payload["money_per_point"] == 1.0
    assert payload["lot_size"] == 50
    assert payload["sizer_reason"] == "RISK_BELOW_ONE_LOT"


def test_filled_position_carries_sized_quantity(tmp_path):
    """After a fill, the tracked StrategyPosition quantity must match
    the size used for the OCO order (not the bare lot_size)."""
    sizer = PositionSizer(capital=500_000, max_risk_pct=1.0)
    orch, broker, _audit = _make(tmp_path, sizer=sizer)
    orch.on_tick("NIFTY", ltp=19800.0, now=_ist(2026, 1, 5, 9, 15, 5))
    orch.on_tick("NIFTY", ltp=19821.0, now=_ist(2026, 1, 5, 9, 16, 0))
    assert "NIFTY" in orch.positions
    assert orch.positions["NIFTY"].quantity == 100
