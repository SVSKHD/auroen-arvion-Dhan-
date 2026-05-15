"""Structured JSON logger emits one JSON object per line."""

import json
import logging
from io import StringIO

from core.logger import _JsonFormatter


def _record(name, level, msg, extra=None):
    rec = logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            setattr(rec, k, v)
    return rec


def test_basic_line_is_valid_json():
    formatter = _JsonFormatter()
    line = formatter.format(_record("aureon", logging.INFO, "hello world"))
    payload = json.loads(line)
    assert payload["msg"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "aureon"
    assert "ts" in payload


def test_extras_serialize_as_top_level_fields():
    formatter = _JsonFormatter()
    line = formatter.format(_record(
        "aureon", logging.INFO, "order placed",
        extra={"symbol": "NIFTY", "qty": 50, "price": 19820.0},
    ))
    payload = json.loads(line)
    assert payload["symbol"] == "NIFTY"
    assert payload["qty"] == 50
    assert payload["price"] == 19820.0


def test_unserializable_extra_falls_back_to_repr():
    class Weird:
        def __repr__(self):
            return "<Weird>"

    formatter = _JsonFormatter()
    line = formatter.format(_record(
        "aureon", logging.WARNING, "hmm",
        extra={"thing": Weird()},
    ))
    payload = json.loads(line)
    assert payload["thing"] == "<Weird>"


def test_ist_offset_in_timestamp():
    """The timestamp must carry the IST offset, not UTC. Operators rely
    on this when grepping logs across hosts.
    """
    formatter = _JsonFormatter()
    line = formatter.format(_record("aureon", logging.INFO, "hello"))
    payload = json.loads(line)
    assert "+05:30" in payload["ts"]
