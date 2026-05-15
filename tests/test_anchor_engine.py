"""Anchor capture acceptance tests.

The brief specifies four clock readings against an anchor_time of 09:15 IST
with a 30-second capture window:

    09:14:59  -> False (before window)
    09:15:00  -> True  (window opens at anchor_time inclusive)
    09:15:29  -> True  (still inside the 30s window)
    09:15:31  -> False (past window)

Plus: kill mid-session, restart, anchor restored from disk.
"""

from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from core.state_store import StateStore
from core.time_utils import IST
from engine.anchor_engine import ANCHORS_KEY, AnchorEngine, AnchorRecord


def _store(tmp_path: Path, name: str = "anchors.json") -> StateStore:
    s = StateStore.__new__(StateStore)
    s.path = tmp_path / name
    return s


def _ist(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=IST)


def test_capture_window_boundary_clocks(tmp_path):
    """Clock walks the four boundary readings without ever capturing.

    Each call asks `should_capture_anchor` for a fresh symbol so the
    "already-captured" check does not mask the window logic.
    """
    engine = AnchorEngine(_store(tmp_path), anchor_time=time(9, 15), capture_window_seconds=30)

    assert engine.should_capture_anchor("NIFTY",     _ist(2026, 1, 5, 9, 14, 59)) is False
    assert engine.should_capture_anchor("NIFTY",     _ist(2026, 1, 5, 9, 15,  0)) is True
    assert engine.should_capture_anchor("BANKNIFTY", _ist(2026, 1, 5, 9, 15, 29)) is True
    assert engine.should_capture_anchor("BANKNIFTY", _ist(2026, 1, 5, 9, 15, 31)) is False


def test_capture_fires_exactly_once_in_window(tmp_path):
    """After the first capture, repeat calls within the window must return False.

    This is the "fires exactly once" guarantee — the live loop polls this gate
    every tick, and we must not re-anchor.
    """
    engine = AnchorEngine(_store(tmp_path), anchor_time=time(9, 15), capture_window_seconds=30)

    now1 = _ist(2026, 1, 5, 9, 15, 5)
    assert engine.should_capture_anchor("NIFTY", now1) is True

    engine.capture_anchor(
        "NIFTY", anchor_price=19800.5, source_bar_time=_ist(2026, 1, 5, 9, 15, 0), now=now1,
    )

    # Still inside the 30s window, but already captured.
    assert engine.should_capture_anchor("NIFTY", _ist(2026, 1, 5, 9, 15, 20)) is False
    assert engine.should_capture_anchor("NIFTY", _ist(2026, 1, 5, 9, 15, 29)) is False
    # Window closed too — also False.
    assert engine.should_capture_anchor("NIFTY", _ist(2026, 1, 5, 9, 16, 0)) is False


def test_outside_trading_hours_never_captures(tmp_path):
    engine = AnchorEngine(_store(tmp_path), anchor_time=time(9, 15), capture_window_seconds=30)

    # Pre-market: anchor window has not technically opened because trading
    # hours start at 09:15; 09:14 is pre-market.
    assert engine.should_capture_anchor("NIFTY", _ist(2026, 1, 5, 9, 14, 30)) is False
    # Post-market: trading hours closed.
    assert engine.should_capture_anchor("NIFTY", _ist(2026, 1, 5, 15, 31, 0)) is False


def test_restart_restores_anchor_from_disk(tmp_path):
    """Capture in one engine instance, instantiate a new one against the same
    StateStore, and verify today's anchor comes back without re-capturing.
    """
    store = _store(tmp_path)
    engine_a = AnchorEngine(store, anchor_time=time(9, 15), capture_window_seconds=30)

    now = _ist(2026, 1, 5, 9, 15, 5)
    rec = engine_a.capture_anchor(
        "NIFTY", anchor_price=19800.5, source_bar_time=_ist(2026, 1, 5, 9, 15, 0), now=now,
    )
    assert isinstance(rec, AnchorRecord)

    # Simulate process restart by building a new engine over the same store.
    engine_b = AnchorEngine(store, anchor_time=time(9, 15), capture_window_seconds=30)

    restored = engine_b.get_anchor("NIFTY", now=_ist(2026, 1, 5, 10, 0, 0))
    assert restored is not None
    assert restored.anchor_price == 19800.5
    assert restored.source_bar_time == _ist(2026, 1, 5, 9, 15, 0)

    # And the gate must NOT fire again post-restart even inside the window.
    assert engine_b.should_capture_anchor("NIFTY", _ist(2026, 1, 5, 9, 15, 15)) is False


def test_stale_anchors_purged_on_new_ist_day(tmp_path):
    """Yesterday's anchors must not survive into today.

    We write a record for yesterday directly into the store, then construct a
    new engine whose `now` is today. The reload step should drop yesterday's
    entry from disk.
    """
    store = _store(tmp_path)
    yesterday_iso = "2026-01-04"
    today_iso = "2026-01-05"

    store.save({
        ANCHORS_KEY: {
            yesterday_iso: {
                "NIFTY": {
                    "anchor_price": 19500.0,
                    "captured_at": "2026-01-04T09:15:00+05:30",
                    "source_bar_time": "2026-01-04T09:15:00+05:30",
                }
            }
        }
    })

    engine = AnchorEngine(store, anchor_time=time(9, 15), capture_window_seconds=30)
    # Force the engine onto today's date.
    engine._roll_day_if_needed(_ist(2026, 1, 5, 9, 0, 0))

    assert engine.get_anchor("NIFTY", now=_ist(2026, 1, 5, 9, 0, 0)) is None
    # Today is empty so the gate is willing to fire when the window opens.
    assert engine.should_capture_anchor("NIFTY", _ist(2026, 1, 5, 9, 15, 0)) is True

    # And disk no longer contains yesterday's date.
    persisted = store.load().get(ANCHORS_KEY, {})
    assert today_iso in persisted or persisted == {today_iso: {}}
    assert yesterday_iso not in persisted


def test_multiple_symbols_independent(tmp_path):
    engine = AnchorEngine(_store(tmp_path), anchor_time=time(9, 15), capture_window_seconds=30)

    now = _ist(2026, 1, 5, 9, 15, 5)
    engine.capture_anchor("NIFTY", 19800.5, _ist(2026, 1, 5, 9, 15, 0), now=now)

    # NIFTY captured but BANKNIFTY's gate should still be open.
    assert engine.should_capture_anchor("NIFTY", now) is False
    assert engine.should_capture_anchor("BANKNIFTY", now) is True


def test_now_ist_is_tz_aware():
    from core.time_utils import now_ist

    n = now_ist()
    assert n.tzinfo is not None
    assert n.utcoffset() == timedelta(hours=5, minutes=30)
