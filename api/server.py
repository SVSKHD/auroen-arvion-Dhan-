"""FastAPI surface for the dashboard and remote monitoring.

Every endpoint reads from the StateStore on disk or from the audit
directory — the orchestrator does not need to know the API exists. All
state writes go through `StateStore` (tmp file + `os.replace`), so the
JSON the API serves is always parseable.

Endpoints:
    GET /health         — liveness
    GET /status         — orchestrator status snapshot (mode, day_pnl,
                          positions, last_tick_at, healthy, paused, …)
    GET /heartbeat      — concise probe for external monitoring
    GET /anchors/today  — today's anchor record per symbol
    GET /positions      — open positions + open OCO pairs
    GET /paper          — paper-mode closed trades
    GET /audit/today    — daily_summary.json + events_count
"""

import json
from pathlib import Path

from fastapi import FastAPI

from core.state_store import StateStore
from core.time_utils import now_ist
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
    """Live orchestrator status. Written by `Orchestrator._publish_status`
    on every tick to `execution_state.json:status`.
    """
    return _EXEC_STORE.load().get("status", {}) or {}


@app.get("/heartbeat")
def heartbeat() -> dict:
    """Concise liveness probe. Computes `last_tick_age_seconds` so an
    external monitor can alarm when the feed goes silent without parsing
    a full status payload.
    """
    s = _EXEC_STORE.load().get("status", {}) or {}
    now = now_ist()
    last_tick_age: float | None = None
    last_tick_at = s.get("last_tick_at")
    if last_tick_at:
        try:
            from datetime import datetime
            ts = datetime.fromisoformat(last_tick_at)
            last_tick_age = round((now - ts).total_seconds(), 2)
        except (ValueError, TypeError):
            last_tick_age = None
    return {
        "ts_ist": now.isoformat(),
        "mode": s.get("mode"),
        "healthy": s.get("healthy", False),
        "last_tick_age_seconds": last_tick_age,
        "feed_connected": last_tick_age is not None and last_tick_age < 60,
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
    status_payload = exec_state.get("status", {}) or {}
    return {
        "positions": status_payload.get("positions", {}) or exec_state.get("positions", {}) or {},
        "oco_pairs": orch_state.get("oco_pairs", {}) or {},
        "day_pnl": status_payload.get("day_pnl", exec_state.get("day_pnl", 0.0)),
        "day_trades": status_payload.get("day_trades", exec_state.get("day_trades", 0)),
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
    """Daily summary plus an events_count (computed by line-counting
    events.jsonl, which can be huge — we don't load it into memory).
    """
    today_iso = now_ist().date().isoformat()
    audit_dir = Path(AUDIT_ROOT) / today_iso
    summary_path = audit_dir / "daily_summary.json"
    events_path = audit_dir / "events.jsonl"

    if not summary_path.exists() and not events_path.exists():
        return {"status": "no_audit_yet", "date": today_iso}

    summary: dict = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text())
        except json.JSONDecodeError:
            summary = {"error": "summary present but unparseable"}

    events_count = 0
    if events_path.exists():
        with open(events_path, "rb") as f:
            for _ in f:
                events_count += 1

    return {
        "date": today_iso,
        "summary": summary,
        "events_count": events_count,
    }
