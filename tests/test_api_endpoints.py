"""FastAPI surface reads from StateStore and the audit directory.

We construct a request via FastAPI's TestClient, populate the relevant
files on disk, and assert the JSON the dashboard sees.
"""

import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from core.state_store import StateStore  # noqa: E402


def _store(tmp_path: Path, name: str) -> StateStore:
    s = StateStore.__new__(StateStore)
    s.path = tmp_path / "state" / name
    s.path.parent.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """Run each test against a fresh `state/` directory so endpoints
    read what THIS test wrote, not global state.
    """
    monkeypatch.chdir(tmp_path)
    # Re-import so module-level StateStores bind to tmp_path's `state/`.
    import importlib

    import core.state_store
    importlib.reload(core.state_store)
    import engine.audit
    importlib.reload(engine.audit)
    import api.server
    importlib.reload(api.server)
    return TestClient(api.server.app)


def test_health_endpoint(app_client):
    r = app_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_anchors_today_empty(app_client):
    r = app_client.get("/anchors/today")
    assert r.status_code == 200
    body = r.json()
    assert "date" in body
    assert body["anchors"] == {}


def test_audit_today_returns_summary_and_events(app_client, tmp_path):
    # Manually write the triplet under state/audit/<today>/.
    from core.time_utils import now_ist
    today = now_ist().date().isoformat()
    audit_dir = tmp_path / "state" / "audit" / today
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "daily_summary.json").write_text(
        json.dumps({"date": today, "mode": "PAPER", "trade_count": 3})
    )
    (audit_dir / "events.jsonl").write_text(
        '{"type":"anchor_captured","ts_ist":"...","symbol":"NIFTY","payload":{}}\n'
        '{"type":"oco_pair_placed","ts_ist":"...","symbol":"NIFTY","payload":{}}\n'
    )

    r = app_client.get("/audit/today")
    body = r.json()
    assert body["summary"]["mode"] == "PAPER"
    assert body["summary"]["trade_count"] == 3
    assert len(body["recent_events"]) == 2
    types = {e["type"] for e in body["recent_events"]}
    assert {"anchor_captured", "oco_pair_placed"} <= types


def test_heartbeat_reads_runtime_status(app_client, tmp_path):
    # Write runtime_status.json so /heartbeat picks it up.
    from core.watchdog import update_status
    update_status({"mode": "PAPER", "day_pnl": 1234.5, "day_trades": 2})
    r = app_client.get("/heartbeat")
    body = r.json()
    assert body["mode"] == "PAPER"
    assert body["day_pnl"] == 1234.5
    assert body["day_trades"] == 2
    assert "ts_ist" in body


def test_positions_endpoint_merges_exec_and_orchestrator_state(app_client, tmp_path):
    exec_store = _store(tmp_path, "execution_state.json")
    exec_store.save({"positions": {"NIFTY": {"side": "LONG"}}, "day_pnl": 500.0, "day_trades": 1})
    orch_store = _store(tmp_path, "orchestrator_state.json")
    orch_store.save({"oco_pairs": {"BANKNIFTY": {"long_order_id": "abc"}}})

    r = app_client.get("/positions")
    body = r.json()
    assert "NIFTY" in body["positions"]
    assert body["day_pnl"] == 500.0
    assert "BANKNIFTY" in body["oco_pairs"]
