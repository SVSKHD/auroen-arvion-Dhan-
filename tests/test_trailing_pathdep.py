"""Phase 2: trailing must not look ahead within a single bar.

Reference scenario: M5 bar Open=100 Low=99 High=110 Close=105,
entry=100, sl=80, tp=130, lock_step=5.

- M5 mode: no exit booked on this bar. SL ratchets to breakeven AFTER
  the exit check, off the close (not the high). The 99 low is harmless.
- M1 mode path 100->99->110: low prints while sl is still 80 -> no exit.
- M1 mode path 100->110->99: sl ratchets to breakeven on the 110 close
  BEFORE the 99 prints -> exit at breakeven (100).
"""

from datetime import datetime, time

import pytest

from engine.strategy_runner import (
    M1Bar,
    StrategyConfig,
    StrategyPosition,
    StrategyRunner,
)


def _config(bar_resolution: str = "M5") -> StrategyConfig:
    return StrategyConfig(
        trigger_dist=20.0,
        tp_dist=30.0,
        sl_dist=20.0,
        lock_step=5.0,
        lock_steps_count=6,
        anchor_time=time(9, 15),
        square_off_time=time(15, 15),
        tick_size=0.05,
        bar_resolution=bar_resolution,  # type: ignore[arg-type]
    )


def _position() -> StrategyPosition:
    return StrategyPosition(
        symbol="SYM",
        side="LONG",
        entry_price=100.0,
        entry_time=datetime(2026, 3, 4, 9, 15),
        quantity=1,
        tp=130.0,
        sl=80.0,
    )


def test_m5_mode_does_not_phantom_exit_on_mixed_bar():
    """OLD bug: ratchet off high=110 -> sl=110, then low=99 books exit.
    NEW: exit checked first (sl=80 vs low=99 -> no hit), then ratchet off
    close=105 -> sl=100. No exit on this bar."""
    cfg = _config("M5")
    runner = StrategyRunner(cfg)
    pos = _position()

    result = runner.evaluate_exit(
        position=pos,
        bar_time=datetime(2026, 3, 4, 9, 20),
        high=110.0,
        low=99.0,
        close=105.0,
    )

    assert result is None, "must not book exit on Open=100 Low=99 High=110 Close=105"
    assert pos.sl == 100.0, f"sl should ratchet to breakeven off close, got {pos.sl}"
    assert pos.lock_idx == 0


def test_m1_path_low_first_then_high_does_not_trigger_sl():
    """Path 100 -> 99 -> 110. SL is still 80 when 99 prints. No exit."""
    cfg = _config("M1")
    runner = StrategyRunner(cfg)
    pos = _position()

    base = datetime(2026, 3, 4, 9, 20)
    m1_bars = [
        M1Bar(time=base, open=100, high=100, low=100, close=100),
        M1Bar(time=base.replace(minute=21), open=100, high=100, low=99, close=99),
        M1Bar(time=base.replace(minute=22), open=99, high=110, low=99, close=110),
    ]

    result = runner.replay_intra_bar(pos, m1_bars)

    assert result is None, "low=99 must not trigger sl=80; path-safe trail must not look ahead"
    # By end of replay the SL has ratcheted (close=110 triggered first lock).
    assert pos.lock_idx == 0
    assert pos.sl == 100.0


def test_m1_path_high_first_then_low_exits_at_breakeven():
    """Path 100 -> 110 -> 99. SL ratchets to breakeven on the 110 close.
    Then 99 hits the trailed sl -> exit at 100, reason TRAIL."""
    cfg = _config("M1")
    runner = StrategyRunner(cfg)
    pos = _position()

    base = datetime(2026, 3, 4, 9, 20)
    m1_bars = [
        M1Bar(time=base, open=100, high=100, low=100, close=100),
        M1Bar(time=base.replace(minute=21), open=100, high=110, low=100, close=110),
        M1Bar(time=base.replace(minute=22), open=110, high=110, low=99, close=99),
    ]

    result = runner.replay_intra_bar(pos, m1_bars)

    assert result is not None
    assert result.exit_price == 100.0
    assert result.exit_reason == "TRAIL"
    assert result.lock_idx == 0


def test_one_step_ratchet_per_call_is_conservative():
    """A single ratchet call advances lock_idx by at most one, even if
    favorable spans multiple steps. Prevents an outsize bar from
    leap-frogging the SL ahead of where price actually walked."""
    cfg = _config("M5")
    runner = StrategyRunner(cfg)
    pos = _position()

    runner.update_trailing(pos, ratchet_price=120.0)  # favorable=20 (4 steps)
    assert pos.lock_idx == 0  # only one step advanced
    assert pos.sl == 100.0

    runner.update_trailing(pos, ratchet_price=120.0)
    assert pos.lock_idx == 1
    assert pos.sl == 105.0


def test_short_side_mirrored():
    """Same scenario flipped: SHORT entry at 100, sl=120, tp=70.
    M5 bar Open=100 High=101 Low=90 Close=95. With NEW logic:
    exits checked first vs sl=120 -> no hit. Ratchet off close=95:
    favorable = 100-95 = 5, idx=0, sl=100 (breakeven). No exit."""
    cfg = _config("M5")
    runner = StrategyRunner(cfg)
    pos = StrategyPosition(
        symbol="SYM",
        side="SHORT",
        entry_price=100.0,
        entry_time=datetime(2026, 3, 4, 9, 15),
        quantity=1,
        tp=70.0,
        sl=120.0,
    )

    result = runner.evaluate_exit(
        position=pos,
        bar_time=datetime(2026, 3, 4, 9, 20),
        high=101.0,
        low=90.0,
        close=95.0,
    )

    assert result is None
    assert pos.lock_idx == 0
    assert pos.sl == 100.0


@pytest.mark.parametrize("bar_resolution", ["M5", "M1"])
def test_square_off_time_still_exits(bar_resolution):
    """Regression: post-fix evaluate_exit must still close at square-off
    when bar_time crosses the configured cutoff."""
    cfg = _config(bar_resolution)
    runner = StrategyRunner(cfg)
    pos = _position()

    result = runner.evaluate_exit(
        position=pos,
        bar_time=datetime(2026, 3, 4, 15, 15),
        high=105.0,
        low=98.0,
        close=102.0,
    )

    assert result is not None
    assert result.exit_reason == "SQUARE_OFF"
    assert result.exit_price == 102.0
