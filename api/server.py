from fastapi import FastAPI

from core.state_store import StateStore
from core.time_utils import now_ist
from core.watchdog import read_status
from engine.anchor_engine import ANCHORS_KEY

app = FastAPI(title="Aureon Arvion Dhan API")

_ANCHOR_STORE = StateStore("anchors.json")
_EXEC_STORE = StateStore("execution_state.json")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/status")
def status() -> dict:
    return read_status()


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
    payload = _EXEC_STORE.load()
    return {
        "positions": payload.get("positions", {}) or {},
        "day_pnl": payload.get("day_pnl", 0.0),
        "day_trades": payload.get("day_trades", 0),
    }


@app.get("/paper")
def paper() -> dict:
    payload = _EXEC_STORE.load()
    return {
        "mode": payload.get("mode"),
        "closed_trades": payload.get("closed_trades", []),
    }
