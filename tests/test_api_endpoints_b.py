"""Pass B API endpoints: /heartbeat and /audit/today.

These read from `StateStore` and the audit directory on disk. Each test
creates the on-disk artifacts the endpoint expects and asserts the JSON
the dashboard sees.
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
    monkeypatch.chdir(tmp_path)
    import importlib

    import core.state_store
    importlib.reload(core.state_store)
    import engine.audit
    importlib.reload(engine.audit)
    import api.server
    importlib.reload(api.server)
    return TestClient(api.server.app)


# --------------------------------------------------------------- heartbeat


def test_heartbeat_no_status_yet(app_client):
    r = app_client.get("/heartbeat")
    body = r.json()
    assert "ts_ist" in body
    assert body["healthy"] is False
    assert body["feed_connected"] is False
    assert body["last_tick_age_seconds"] is None


def test_heartbeat_reads_status_payload(app_client, tmp_path):
    from datetime import timedelta

    from core.time_utils import now_ist

    fresh_tick = (now_ist() - timedelta(seconds=2)).isoformat()
    exec_store = _store(tmp_path, "execution_state.json")
    exec_store.save({"status": {
        "mode": "PAPER", "healthy": True,
        "last_tick_at": fresh_tick, "day_pnl": 100.0,
    }})
    r = app_client.get("/heartbeat")
    body = r.json()
    assert body["mode"] == "PAPER"
    assert body["healthy"] is True
    assert body["feed_connected"] is True
    assert body["last_tick_age_seconds"] is not None
    assert body["last_tick_age_seconds"] < 60


def test_heartbeat_marks_stale_feed_disconnected(app_client, tmp_path):
    from datetime import timedelta

    from core.time_utils import now_ist

    stale = (now_ist() - timedelta(minutes=5)).isoformat()
    exec_store = _store(tmp_path, "execution_state.json")
    exec_store.save({"status": {
        "mode": "PAPER", "healthy": True, "last_tick_at": stale,
    }})
    r = app_client.get("/heartbeat")
    body = r.json()
    assert body["feed_connected"] is False
    assert body["last_tick_age_seconds"] > 60


# --------------------------------------------------------------- /audit/today


def test_audit_today_no_artifacts_yet(app_client):
    r = app_client.get("/audit/today")
    body = r.json()
    assert body["status"] == "no_audit_yet"


def test_audit_today_returns_summary_and_events_count(app_client, tmp_path):
    from core.time_utils import now_ist

    today = now_ist().date().isoformat()
    audit_dir = tmp_path / "state" / "audit" / today
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "daily_summary.json").write_text(
        json.dumps({"date": today, "mode": "PAPER", "trade_count": 3})
    )
    (audit_dir / "events.jsonl").write_text(
        '{"type":"anchor_captured"}\n{"type":"oco_pair_placed"}\n'
        '{"type":"sl_modified"}\n{"type":"square_off"}\n'
    )

    r = app_client.get("/audit/today")
    body = r.json()
    assert body["summary"]["mode"] == "PAPER"
    assert body["summary"]["trade_count"] == 3
    # `events_count` counts lines without loading file into memory.
    assert body["events_count"] == 4


def test_audit_today_handles_unparseable_summary(app_client, tmp_path):
    from core.time_utils import now_ist

    today = now_ist().date().isoformat()
    audit_dir = tmp_path / "state" / "audit" / today
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "daily_summary.json").write_text("garbage{not json")

    r = app_client.get("/audit/today")
    body = r.json()
    assert "error" in body["summary"]
