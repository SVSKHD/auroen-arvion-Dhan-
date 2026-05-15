"""Structured logger.

One JSON object per line. Fields:
    ts        ISO-8601 IST timestamp (+05:30)
    level     INFO / WARNING / ERROR / DEBUG
    logger    logger name
    msg       the formatted message
    plus any extra fields the caller passes via `extra={...}`

JSON-per-line lets us `jq` over logs, feed them into audit tooling, and
keep human-readable messages in a `msg` field. Set `AUREON_LOG_FORMAT=plain`
in the environment to fall back to the legacy pipe-delimited format for
local debugging.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo  # stdlib >=3.9
    _IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    _IST = None

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

_RESERVED_RECORD_FIELDS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime",
}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if _IST is not None:
            ts = datetime.fromtimestamp(record.created, tz=_IST).isoformat()
        else:  # pragma: no cover
            ts = datetime.utcfromtimestamp(record.created).isoformat() + "Z"
        payload = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Extras passed via logger.info(..., extra={"k": v}) appear as
        # top-level attributes on the record; surface them.
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_FIELDS or key.startswith("_"):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except TypeError:
                payload[key] = repr(value)
        return json.dumps(payload, default=str)


def _use_json_format() -> bool:
    return os.environ.get("AUREON_LOG_FORMAT", "json").lower() != "plain"


def setup_logger(name: str = "aureon_dhan", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)

    if _use_json_format():
        formatter: logging.Formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )

    file_handler = logging.FileHandler(LOG_DIR / "bot.log")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def get_logger(name: str = "aureon_dhan") -> logging.Logger:
    return setup_logger(name)
