"""Path-dependence acceptance tests for the trailing-lock fix.

Pre-fix, evaluate_exit ratcheted SL from the bar's `high` and then compared
`low` to the new SL on the same bar, yielding phantom TRAIL exits that could
never happen on a real intra-bar path. Post-fix:

  - M5 mode trails only from the bar's close, AFTER the exit check, so the
    SL applied to a bar comes from the previous bar's close.
  - M1 mode replays sub-bars through evaluate_exit, so the intra-bar order of
    high vs low actually matters.

The synthetic M5 bar (O=100 L=99 H=110 C=105) reproduces the pre-fix bug:
old code would have ratcheted SL to 105 from the high, then exited at 105
because 99 <= 105. Post-fix it never does.
"""

from datetime import datetime, time

from engine.strategy_runner import (
    M1Bar,
    StrategyConfig,
    StrategyPosition,
    StrategyRunner,
)


def _config() -> StrategyConfig:
    return StrategyConfig(
        trigger_dist=20.0,
        tp_dist=30.0,
        sl_dist=40.0,
        lock_step=5.0,
        lock_steps_count=6,
        anchor_time=time(9, 15),
        square_off_time=time(15, 15),
        tick_size=0.05,
    )


def _long_position() -> StrategyPosition:
    return StrategyPosition(
        symbol="X",
        side="LONG",
        entry_price=100.0,
        entry_time=datetime(2026, 1, 5, 9, 20),
        quantity=1,
        tp=130.0,
        sl=80.0,
    )


def test_m5_synthetic_bar_does_not_phantom_trail_exit():
    """Pre-fix bug: O=100 L=99 H=110 C=105 booked a TRAIL exit at 105.

    Mechanism (pre-fix): update_trailing(high=110) ratcheted SL to 105;
    then sl_hit = (low=99 <= 105) -> True -> exit at 105.
    Post-fix: exit check runs first with SL=80, no hit. Trailing then updates
    from close=105 to SL=100 for the *next* bar. No exit booked on this bar.
    """
    runner = StrategyRunner(_config())
    position = _long_position()
    bar_time = datetime(2026, 1, 5, 9, 25)

    result = runner.evaluate_exit(
        position=position,
        bar_time=bar_time,
        high=110.0,
        low=99.0,
        close=105.0,
    )

    assert result is None, "No exit should be booked on this bar; pre-fix booked TRAIL@105"
    assert position.sl == 100.0, "Close-based trail: favorable=5 -> lock_idx=0 -> SL=entry (breakeven)"
    assert position.lock_idx == 0


def test_m1_low_first_high_later_no_exit():
    """Path A: price prints low=99 BEFORE high=110.

    When low=99 is encountered the SL is still 80 (no ratchet yet), so no exit
    fires. By the time high prints later in the M5 window, low=99 is behind us
    and cannot retroactively hit a higher SL. Final state: position open.
    """
    runner = StrategyRunner(_config())
    position = _long_position()
    base = datetime(2026, 1, 5, 9, 20)

    m1_bars = [
        M1Bar(bar_time=base.replace(minute=20), high=100.0, low=99.0, close=99.5),
        M1Bar(bar_time=base.replace(minute=21), high=101.0, low=99.5, close=101.0),
        M1Bar(bar_time=base.replace(minute=22), high=104.0, low=101.0, close=104.0),
        M1Bar(bar_time=base.replace(minute=23), high=108.0, low=104.0, close=108.0),
        M1Bar(bar_time=base.replace(minute=24), high=110.0, low=108.0, close=110.0),
    ]

    result = runner.replay_intra_bar(position, m1_bars)

    assert result is None, "Low=99 prints before any trail engages; no exit on this M5 window"


def test_m1_high_first_low_later_books_trail_exit_at_breakeven():
    """Path B: price prints high first, ratcheting SL to breakeven, then drops
    through SL.

    The high reaches 105 in sub-bar 2 (close=105, favorable=5, lock_idx=0,
    SL=entry=100). Sub-bar 3 then prints low=99 with SL=100 already locked,
    so low <= SL fires a TRAIL exit at 100. The later 110 print in the same
    sub-bar is irrelevant because the exit happens at SL on the way down.
    """
    runner = StrategyRunner(_config())
    position = _long_position()
    base = datetime(2026, 1, 5, 9, 20)

    m1_bars = [
        M1Bar(bar_time=base.replace(minute=20), high=103.0, low=100.0, close=103.0),
        M1Bar(bar_time=base.replace(minute=21), high=105.0, low=103.0, close=105.0),
        M1Bar(bar_time=base.replace(minute=22), high=110.0, low=99.0, close=99.0),
    ]

    result = runner.replay_intra_bar(position, m1_bars)

    assert result is not None
    assert result.exit_price == 100.0, "Exit at trailed SL (breakeven), not at the 110 print"
    assert result.exit_reason == "TRAIL"
    assert result.points_pnl == 0.0


def test_phase1_parity_fixture_still_in_parity():
    """Both BacktestEngine and ExecutionEngine route through StrategyRunner,
    so the Phase 2 trailing fix applies to both. The existing parity test
    (tests/test_runner_parity.py) is the formal guarantee; here we just sanity-
    check that the runner produces identical state when driven twice on the
    same bar sequence.
    """
    cfg = _config()
    r1 = StrategyRunner(cfg)
    r2 = StrategyRunner(cfg)
    p1 = _long_position()
    p2 = _long_position()
    bar_time = datetime(2026, 1, 5, 9, 25)

    out1 = r1.evaluate_exit(p1, bar_time, high=108.0, low=99.0, close=106.0)
    out2 = r2.evaluate_exit(p2, bar_time, high=108.0, low=99.0, close=106.0)

    assert out1 is None and out2 is None
    assert p1.sl == p2.sl
    assert p1.lock_idx == p2.lock_idx
    assert p1.max_favorable_move == p2.max_favorable_move
