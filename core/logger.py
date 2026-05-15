"""Structured JSON logger.

One JSON object per line. Schema:

    {"ts_ist":"2026-05-15T09:15:03+05:30",
     "level":"INFO",
     "logger":"orchestrator",
     "event":"anchor_captured",
     "message":"...",            # optional, the formatted human message
     "details":{...}}             # optional, structured event payload

Two call styles coexist:

    1. Legacy / printf-style (Phase 1–6a code uses this everywhere):
           logger.info("ANCHOR | day=%s symbol=%s price=%.2f", date, sym, price)
       Wrapped to produce {"event":"legacy", "message":"ANCHOR | day=..."}.
       Existing log-parsing tooling that grepped `bot.log` still works.

    2. Structured event:
           logger.info("anchor_captured", symbol="NIFTY", price=19800.5)
       Produces {"event":"anchor_captured", "details":{"symbol":"NIFTY",
       "price":19800.5}}. New call sites should use this form.

`LOG_FORMAT=plain` falls back to the legacy pipe-delimited human format
for local dev (`tail -f logs/bot.log` is readable that way). Defaults to
`json` so any tooling downstream can `jq` the output.
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo  # stdlib >=3.9
    _IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - 3.8 fallback
    _IST = None

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

_RESERVED_RECORD_FIELDS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime",
}

# Detect "structured event" calls — the first positional is a single
# word/snake_case identifier with no formatting placeholders. Anything
# else is treated as a legacy printf-style message.
_EVENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _is_structured_event(msg: Any, args: tuple) -> bool:
    if args:
        return False
    if not isinstance(msg, str):
        return False
    if "%" in msg or "{" in msg or " " in msg:
        return False
    return bool(_EVENT_NAME_RE.match(msg))


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if _IST is not None:
            ts = datetime.fromtimestamp(record.created, tz=_IST).isoformat()
        else:  # pragma: no cover
            ts = datetime.utcfromtimestamp(record.created).isoformat() + "Z"

        # Pull "extra" fields off the record. Anything attached via
        # logger.info(..., extra={"k": v}) lands here.
        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_FIELDS or key.startswith("_"):
                continue
            try:
                json.dumps(value)
                extras[key] = value
            except TypeError:
                extras[key] = repr(value)

        if _is_structured_event(record.msg, record.args):
            event = record.msg
            payload = {
                "ts_ist": ts,
                "level": record.levelname,
                "logger": record.name,
                "event": event,
                "details": extras or None,
            }
        else:
            payload = {
                "ts_ist": ts,
                "level": record.levelname,
                "logger": record.name,
                "event": "legacy",
                "message": record.getMessage(),
            }
            if extras:
                payload["details"] = extras

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def _use_plain_format() -> bool:
    return os.environ.get("LOG_FORMAT", "json").lower() == "plain"


def setup_logger(name: str = "aureon_dhan", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)

    if _use_plain_format():
        formatter: logging.Formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )
    else:
        formatter = _JsonFormatter()

    file_handler = logging.FileHandler(LOG_DIR / "bot.log")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def get_logger(name: str = "aureon_dhan") -> logging.Logger:
    return setup_logger(name)
