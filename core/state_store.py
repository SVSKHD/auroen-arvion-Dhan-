import json
import os
from pathlib import Path
from typing import Any

STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)


class StateStore:
    def __init__(self, filename: str = "runtime_state.json") -> None:
        self.path = STATE_DIR / filename

    def save(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(".tmp")

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        os.replace(tmp_path, self.path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}

        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)
