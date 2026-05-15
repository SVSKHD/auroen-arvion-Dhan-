"""FastAPI surface for the Aureon Arvion Dhan agent.

Every endpoint reads from StateStore (atomic writes guarantee the JSON is
always valid) or directly from the daily audit triplet on disk. The
orchestrator does not need to know about the API — the contract is the
files on disk.
"""

import json
from pathlib import Path

from fastapi import FastAPI

from core.state_store import StateStore
from core.time_utils import now_ist
from core.watchdog import read_status
from engine.anchor_engine import ANCHORS_KEY
from engine.audit import AUDIT_ROOT

app = FastAPI(title="Aureon Arvion Dhan API")

_ANCHOR_STORE = StateStore("anchors.json")
_EXEC_STORE = StateStore("execution_state.json")
_ORCH_STORE = StateStore("orchestrator_state.json")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/status")
def status() -> dict:
    return read_status()


@app.get("/heartbeat")
def heartbeat() -> dict:
    """Liveness with the most recent runtime fields the orchestrator
    publishes. Used by the dashboard and by external monitoring.
    """
    payload = read_status()
    return {
        "ts_ist": now_ist().isoformat(),
        "mode": payload.get("mode"),
        "day_pnl": payload.get("day_pnl"),
        "day_trades": payload.get("day_trades"),
        "positions": payload.get("positions") or {},
    }


@app.get("/anchors/today")
def anchors_today() -> dict:
    today_iso = now_ist().date().isoformat()
    payload = _ANCHOR_STORE.load()
    return {
        "date": today_iso,
        "anchors": (payload.get(ANCHORS_KEY, {}) or {}).get(today_iso, {}),
    }


@app.get("/positions")
def positions() -> dict:
    exec_state = _EXEC_STORE.load()
    orch_state = _ORCH_STORE.load()
    return {
        "positions": exec_state.get("positions", {}) or {},
        "oco_pairs": orch_state.get("oco_pairs", {}) or {},
        "day_pnl": exec_state.get("day_pnl", 0.0),
        "day_trades": exec_state.get("day_trades", 0),
    }


@app.get("/paper")
def paper() -> dict:
    payload = _EXEC_STORE.load()
    return {
        "mode": payload.get("mode"),
        "closed_trades": payload.get("closed_trades", []),
    }


@app.get("/audit/today")
def audit_today() -> dict:
    """Return today's audit summary + tail of events.jsonl.

    The full events log can be large; the dashboard typically wants a
    rolling window. Adjust `tail_lines` if you need more.
    """
    today_iso = now_ist().date().isoformat()
    dir = Path(AUDIT_ROOT) / today_iso
    summary_path = dir / "daily_summary.json"
    events_path = dir / "events.jsonl"

    summary = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text())
        except json.JSONDecodeError:
            summary = {"error": "summary file present but unparseable"}

    events: list[dict] = []
    if events_path.exists():
        tail_lines = 200
        with open(events_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        for line in all_lines[-tail_lines:]:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    return {
        "date": today_iso,
        "summary": summary,
        "recent_events": events,
    }
