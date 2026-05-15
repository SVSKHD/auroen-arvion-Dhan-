"""Structured JSON logger emits one JSON object per line.

Two call styles must coexist:
  - Legacy: `logger.info("ANCHOR | day=%s", ...)` → `event: legacy`
  - Structured: `logger.info("anchor_captured", extra={...})` → `event: anchor_captured`
"""

import json
import logging

from core.logger import _JsonFormatter, _is_structured_event


def _record(name, level, msg, args=(), extras=None):
    rec = logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1,
        msg=msg, args=args, exc_info=None,
    )
    if extras:
        for k, v in extras.items():
            setattr(rec, k, v)
    return rec


def test_basic_legacy_line_valid_json():
    """`logger.info("Hello world")` is treated as legacy because it
    has whitespace and is not snake_case-only."""
    formatter = _JsonFormatter()
    line = formatter.format(_record("aureon", logging.INFO, "Hello world"))
    payload = json.loads(line)
    assert payload["event"] == "legacy"
    assert payload["message"] == "Hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "aureon"
    assert "+05:30" in payload["ts_ist"]


def test_structured_event_emits_event_field():
    formatter = _JsonFormatter()
    line = formatter.format(_record(
        "orchestrator", logging.INFO, "anchor_captured",
        extras={"symbol": "NIFTY", "price": 19800.5, "lot_size": 50},
    ))
    payload = json.loads(line)
    assert payload["event"] == "anchor_captured"
    assert payload["details"] == {"symbol": "NIFTY", "price": 19800.5, "lot_size": 50}
    assert "message" not in payload


def test_printf_style_legacy_preserved():
    """Old call site `logger.info("ENTRY | symbol=%s side=%s", ...)`
    keeps producing a `legacy` event with the formatted message — the
    existing tooling/log-parsing scripts grep the message body."""
    formatter = _JsonFormatter()
    line = formatter.format(_record(
        "execution_engine", logging.INFO,
        "ENTRY | symbol=%s side=%s qty=%d",
        args=("NIFTY", "LONG", 50),
    ))
    payload = json.loads(line)
    assert payload["event"] == "legacy"
    assert payload["message"] == "ENTRY | symbol=NIFTY side=LONG qty=50"


def test_unserializable_extras_fall_back_to_repr():
    class Weird:
        def __repr__(self):
            return "<Weird>"

    formatter = _JsonFormatter()
    line = formatter.format(_record(
        "aureon", logging.WARNING, "weird_event",
        extras={"thing": Weird()},
    ))
    payload = json.loads(line)
    assert payload["details"]["thing"] == "<Weird>"


def test_exception_info_attached():
    formatter = _JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        rec = logging.LogRecord(
            name="x", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="something_broke", args=(), exc_info=sys.exc_info(),
        )
    line = formatter.format(rec)
    payload = json.loads(line)
    assert "exc" in payload
    assert "ValueError" in payload["exc"]
    assert payload["event"] == "something_broke"


def test_is_structured_event_classifier():
    """The classifier decides whether a call is "event-style" or
    "legacy printf-style"."""
    assert _is_structured_event("anchor_captured", ())
    assert _is_structured_event("oco_pair_placed", ())
    # Args present → legacy.
    assert not _is_structured_event("anchor_captured", ("x",))
    # Whitespace → legacy.
    assert not _is_structured_event("Anchor captured", ())
    # Formatting placeholder → legacy.
    assert not _is_structured_event("event=%s", ())
    # Non-string → legacy.
    assert not _is_structured_event(42, ())
