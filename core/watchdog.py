import json
import time
from pathlib import Path

STATUS_FILE = Path("runtime_status.json")


def update_status(payload: dict) -> None:
    payload["updated_at"] = time.time()
    STATUS_FILE.write_text(json.dumps(payload, indent=2))


def read_status() -> dict:
    if not STATUS_FILE.exists():
        return {}

    return json.loads(STATUS_FILE.read_text())
