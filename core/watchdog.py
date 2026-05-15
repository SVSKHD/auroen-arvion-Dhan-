"""Runtime status writer. Backed by StateStore for atomic disk writes.

The previous `STATUS_FILE.write_text(...)` implementation was non-atomic;
a crash mid-write would leave a truncated JSON file that the API server
could not parse. Routing through StateStore (tmp file + os.replace) makes
the runtime_status.json read by the API server always valid.
"""

from typing import Any

from core.state_store import StateStore
from core.time_utils import now_ist

STATUS_KEY = "runtime_status"
_STORE = StateStore("runtime_status.json")


def update_status(payload: dict[str, Any]) -> None:
    payload = dict(payload)
    payload["updated_at"] = now_ist().isoformat()
    existing = _STORE.load()
    existing[STATUS_KEY] = payload
    _STORE.save(existing)


def read_status() -> dict[str, Any]:
    return _STORE.load().get(STATUS_KEY, {}) or {}
