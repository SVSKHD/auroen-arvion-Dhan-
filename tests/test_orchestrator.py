"""Phase 5 orchestrator acceptance tests.

These tests drive `Orchestrator.on_tick` directly with an injected IST
clock and a PaperBroker, so they do not need a live feed or any
networking. The acceptance pinpoints from the brief:

  - PAPER mode bootstraps without error
  - OCO stop pair is placed after anchor capture
  - One side filling triggers cancel of the peer (audit emits both
    `order_filled` and `peer_cancelled`)
  - Trailing SL adjusts on the lagged-close 5-minute boundary and
    persists via `broker.modify_sl`
  - Square-off closes any open position at the configured time
  - Daily audit triplet (events.jsonl, daily_summary.json,
    state_snapshot.json) emits to `state/audit/<date>/`
  - Restart re-hydrates OCO pairs from disk so a mid-session crash does
    not double-issue orders
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
from engine.orchestrator import OCO_PAIRS_KEY, Orchestrator, _SymbolMeta
from engine.strategy_runner import StrategyConfig
from risk.guardrails import GuardRails


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


def _make_orchestrator(tmp_path: Path, mode: str = "PAPER"):
    audit_root = tmp_path / "audit"
    audit_root.mkdir()
    audit = DailyAudit(date_iso="2026-01-05", root=audit_root)
    broker = PaperBroker()
    return Orchestrator(
        mode=mode,
        broker=broker,
        anchor_engine=AnchorEngine(
            _store(tmp_path, "anchors.json"),
            anchor_time=time(9, 15),
            capture_window_seconds=30,
        ),
        strategy_config=_config(),
        guardrails=GuardRails(max_daily_loss=5000.0, max_trades_per_day=3),
        symbols=[
            _SymbolMeta(symbol="NIFTY", security_id="49081", exchange_segment="NSE_FNO", lot_size=50),
        ],
        state_store=_store(tmp_path, "orchestrator_state.json"),
        audit=audit,
    ), broker, audit


# --------------------------------------------------------------- bootstrap


def test_bootstrap_writes_audit_directory(tmp_path):
    orch, _broker, audit = _make_orchestrator(tmp_path)
    orch.shutdown()
    assert audit.events_path.parent.exists()
    assert audit.summary_path.exists()


# --------------------------------------------------------------- anchor + entry


def test_anchor_then_oco_pair_placed(tmp_path):
    orch, broker, audit = _make_orchestrator(tmp_path)

    # 09:15:05 — first tick inside anchor window
    orch.on_tick("NIFTY", ltp=19800.0, now=_ist(2026, 1, 5, 9, 15, 5))

    record = orch.anchor_engine.get_anchor("NIFTY", now=_ist(2026, 1, 5, 9, 15, 5))
    assert record is not None
    assert record.anchor_price == 19800.0

    open_orders = broker.get_open_orders()
    assert len(open_orders) == 2
    kinds = {o.side for o in open_orders}
    assert kinds == {"LONG", "SHORT"}
    long_order = next(o for o in open_orders if o.side == "LONG")
    short_order = next(o for o in open_orders if o.side == "SHORT")
    assert long_order.trigger_price == pytest.approx(19820.0)  # 19800 + 20
    assert short_order.trigger_price == pytest.approx(19780.0)  # 19800 - 20

    events = _read_events(audit)
    assert any(e["type"] == "anchor_captured" for e in events)
    assert any(e["type"] == "oco_pair_placed" for e in events)


def test_oco_peer_cancel_on_first_fill(tmp_path):
    orch, broker, audit = _make_orchestrator(tmp_path)
    orch.on_tick("NIFTY", ltp=19800.0, now=_ist(2026, 1, 5, 9, 15, 5))

    # Cross the long trigger (19820) on the next tick.
    orch.on_tick("NIFTY", ltp=19822.0, now=_ist(2026, 1, 5, 9, 16, 0))

    open_orders = broker.get_open_orders()
    assert len(open_orders) == 0, "Peer must have been cancelled after fill"
    assert "NIFTY" in orch.positions, "Filled side must create a tracked position"
    position = orch.positions["NIFTY"]
    assert position.side == "LONG"
    assert position.entry_price == pytest.approx(19820.0)

    events = _read_events(audit)
    assert any(e["type"] == "order_filled" for e in events)
    assert any(e["type"] == "peer_cancelled" for e in events)
    # OCO pair removed from in-memory and from disk.
    assert "NIFTY" not in orch.oco_pairs


# --------------------------------------------------------------- trailing


def test_trailing_sl_modified_on_lagged_close_boundary(tmp_path):
    orch, broker, audit = _make_orchestrator(tmp_path)
    # Setup: anchor + fill long at 19820.
    orch.on_tick("NIFTY", ltp=19800.0, now=_ist(2026, 1, 5, 9, 15, 5))
    orch.on_tick("NIFTY", ltp=19822.0, now=_ist(2026, 1, 5, 9, 16, 0))

    # Build a 5-min window that closes above lock_step.
    orch.on_tick("NIFTY", ltp=19825.0, now=_ist(2026, 1, 5, 9, 16, 30))
    orch.on_tick("NIFTY", ltp=19828.0, now=_ist(2026, 1, 5, 9, 18, 0))
    # Close the bar at 19825 (favorable=5 -> breakeven trail).
    orch.on_tick("NIFTY", ltp=19825.0, now=_ist(2026, 1, 5, 9, 19, 0))
    # Now cross the next 5-min boundary so the 9:15-9:20 bar "closes".
    orch.on_tick("NIFTY", ltp=19826.0, now=_ist(2026, 1, 5, 9, 20, 5))

    position = orch.positions["NIFTY"]
    assert position.lock_idx >= 0
    assert position.sl >= 19820.0  # ratcheted to at least breakeven
    paper_pos = broker.get_open_position("NIFTY")
    assert paper_pos.sl == position.sl, "Broker SL must mirror runner SL"

    events = _read_events(audit)
    assert any(e["type"] == "sl_modified" for e in events)


# --------------------------------------------------------------- square-off


def test_square_off_closes_open_position(tmp_path):
    orch, broker, audit = _make_orchestrator(tmp_path)
    orch.on_tick("NIFTY", ltp=19800.0, now=_ist(2026, 1, 5, 9, 15, 5))
    orch.on_tick("NIFTY", ltp=19822.0, now=_ist(2026, 1, 5, 9, 16, 0))

    # Fast-forward to square-off.
    orch.on_tick("NIFTY", ltp=19850.0, now=_ist(2026, 1, 5, 15, 15, 1))

    assert "NIFTY" not in orch.positions
    assert broker.get_open_position("NIFTY") is None
    assert orch.day_trades == 1
    assert orch.day_pnl > 0  # entry 19820 -> exit 19850, +30 points × 50 qty

    events = _read_events(audit)
    assert any(e["type"] == "square_off" for e in events)


def test_pre_squareoff_cancels_unfilled_oco_pair(tmp_path):
    orch, broker, _audit = _make_orchestrator(tmp_path)
    orch.on_tick("NIFTY", ltp=19800.0, now=_ist(2026, 1, 5, 9, 15, 5))
    assert len(broker.get_open_orders()) == 2

    # square_off=15:15, pre-squareoff buffer=5min, so trigger at 15:10.
    orch.on_tick("NIFTY", ltp=19800.0, now=_ist(2026, 1, 5, 15, 10, 30))

    assert broker.get_open_orders() == []
    assert "NIFTY" not in orch.oco_pairs


# --------------------------------------------------------------- audit


def test_shutdown_emits_daily_summary_and_snapshot(tmp_path):
    orch, _broker, audit = _make_orchestrator(tmp_path)
    orch.on_tick("NIFTY", ltp=19800.0, now=_ist(2026, 1, 5, 9, 15, 5))
    orch.on_tick("NIFTY", ltp=19822.0, now=_ist(2026, 1, 5, 9, 16, 0))
    orch.on_tick("NIFTY", ltp=19855.0, now=_ist(2026, 1, 5, 15, 15, 1))
    orch.shutdown()

    assert audit.summary_path.exists()
    summary = json.loads(audit.summary_path.read_text())
    assert summary["mode"] == "PAPER"
    assert summary["trade_count"] == 1
    assert summary["trades"][0]["symbol"] == "NIFTY"
    assert summary["trades"][0]["exit_reason"] == "SQUARE_OFF"
    assert audit.events_path.exists()
    assert audit.snapshot_path.exists()


# --------------------------------------------------------------- restart


def test_restart_reloads_oco_pair_from_disk(tmp_path):
    orch_a, broker_a, _audit_a = _make_orchestrator(tmp_path)
    orch_a.on_tick("NIFTY", ltp=19800.0, now=_ist(2026, 1, 5, 9, 15, 5))
    persisted = orch_a.state_store.load()
    assert OCO_PAIRS_KEY in persisted
    assert "NIFTY" in persisted[OCO_PAIRS_KEY]

    # Simulate restart: brand new broker (orders lost — we'd reconcile
    # via DhanBroker.reconcile_pending_intents() in LIVE), but the
    # orchestrator must still re-hydrate the pair from disk so it does
    # NOT post a fresh OCO pair on the next tick.
    audit_root = tmp_path / "audit2"
    audit_root.mkdir()
    audit_b = DailyAudit(date_iso="2026-01-05", root=audit_root)
    broker_b = PaperBroker()
    orch_b = Orchestrator(
        mode="PAPER",
        broker=broker_b,
        anchor_engine=AnchorEngine(
            orch_a.anchor_engine.state_store,
            anchor_time=time(9, 15),
            capture_window_seconds=30,
        ),
        strategy_config=_config(),
        guardrails=GuardRails(max_daily_loss=5000.0, max_trades_per_day=3),
        symbols=[
            _SymbolMeta(symbol="NIFTY", security_id="49081", exchange_segment="NSE_FNO", lot_size=50),
        ],
        state_store=orch_a.state_store,
        audit=audit_b,
    )
    assert "NIFTY" in orch_b.oco_pairs
    # The next tick must not double-issue an OCO pair.
    orch_b.on_tick("NIFTY", ltp=19801.0, now=_ist(2026, 1, 5, 9, 17, 0))
    assert len(broker_b.get_open_orders()) == 0, "Restart must not re-post orders"


# --------------------------------------------------------------- helpers


def _read_events(audit: DailyAudit) -> list[dict]:
    if not audit.events_path.exists():
        return []
    return [json.loads(line) for line in audit.events_path.read_text().splitlines() if line.strip()]
