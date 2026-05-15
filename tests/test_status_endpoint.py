"""Orchestrator writes live status to execution_state.json:status;
/status reads it back. The watchdog migration is complete when this
test passes and there are no remaining `watchdog` imports.
"""

import json
from datetime import datetime, time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from brokers.paper.paper_broker import PaperBroker  # noqa: E402
from core.state_store import StateStore  # noqa: E402
from core.time_utils import IST  # noqa: E402
from engine.anchor_engine import AnchorEngine  # noqa: E402
from engine.audit import DailyAudit  # noqa: E402
from engine.orchestrator import Orchestrator, _SymbolMeta  # noqa: E402
from engine.strategy_runner import StrategyConfig  # noqa: E402
from risk.guardrails import GuardRails  # noqa: E402


def _ist(year, month, day, hour, minute, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=IST)


def _store(tmp_path: Path, name: str) -> StateStore:
    s = StateStore.__new__(StateStore)
    s.path = tmp_path / "state" / name
    s.path.parent.mkdir(parents=True, exist_ok=True)
    return s


def test_watchdog_module_is_deleted():
    """The Phase 3 leftover is gone — no module to import."""
    with pytest.raises(ModuleNotFoundError):
        __import__("core.watchdog")


def test_orchestrator_publishes_status_on_each_tick(tmp_path):
    audit_root = tmp_path / "audit"
    audit_root.mkdir()
    exec_store = _store(tmp_path, "execution_state.json")
    orch = Orchestrator(
        mode="PAPER",
        broker=PaperBroker(),
        anchor_engine=AnchorEngine(
            _store(tmp_path, "anchors.json"),
            anchor_time=time(9, 15),
            capture_window_seconds=30,
        ),
        strategy_config=StrategyConfig(
            trigger_dist=20.0, tp_dist=30.0, sl_dist=40.0,
            lock_step=5.0, lock_steps_count=6,
            anchor_time=time(9, 15), square_off_time=time(15, 15),
            tick_size=0.05,
        ),
        guardrails=GuardRails(max_daily_loss=5000.0, max_trades_per_day=3),
        symbols=[_SymbolMeta("NIFTY", "49081", "NSE_FNO", lot_size=50, sl_dist=40.0)],
        state_store=_store(tmp_path, "orchestrator_state.json"),
        audit=DailyAudit("2026-01-05", root=audit_root),
        status_store=exec_store,
    )

    # Before any tick — status is whatever the constructor wrote (none).
    snapshot = orch.status_snapshot()
    assert snapshot["mode"] == "PAPER"
    assert snapshot["healthy"] is False  # no ticks yet

    orch.on_tick("NIFTY", ltp=19800.0, now=_ist(2026, 1, 5, 9, 15, 5))

    payload = exec_store.load()
    assert "status" in payload
    status = payload["status"]
    assert status["mode"] == "PAPER"
    assert status["healthy"] is True
    assert status["last_tick_at"] is not None
    assert status["day_pnl"] == 0.0
    assert "NIFTY" in status["symbols"]


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import importlib

    import core.state_store
    importlib.reload(core.state_store)
    import api.server
    importlib.reload(api.server)
    return TestClient(api.server.app)


def test_status_endpoint_serves_orchestrator_payload(app_client, tmp_path):
    exec_store = _store(tmp_path, "execution_state.json")
    exec_store.save({"status": {
        "ts_ist": "2026-01-05T09:15:00+05:30",
        "mode": "PAPER", "day_pnl": 1234.5, "day_trades": 2,
        "healthy": True, "last_tick_at": "2026-01-05T09:15:00+05:30",
        "positions": {}, "symbols": ["NIFTY"], "paused": False,
    }})
    r = app_client.get("/status")
    body = r.json()
    assert body["mode"] == "PAPER"
    assert body["day_pnl"] == 1234.5
    assert body["healthy"] is True


def test_status_endpoint_empty_when_no_orchestrator_state(app_client):
    """No file on disk means an empty dict — not a 500."""
    r = app_client.get("/status")
    assert r.status_code == 200
    assert r.json() == {}
