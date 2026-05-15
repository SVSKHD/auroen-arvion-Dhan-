"""DrawdownTracker — shared between BacktestEngine and DailyAudit.

The tracker is the single place that decides what `max_drawdown_money`
and `max_drawdown_pct` mean. If the two callers ever drift on this, the
audit will silently disagree with the backtest.
"""

from engine.metrics import DrawdownTracker


def test_synthetic_sequence_drawdown():
    """Trade PnL stream: +50, -20, +10, -30, +80.

    Cumulative:    50,  30,  40,  10,  90
    Running peak:  50,  50,  50,  50,  90
    Drawdown:       0,  20,  10,  40,   0

    Max drawdown is 40 (after the -30 trade brings cumulative to 10
    against the peak of 50).
    """
    dd = DrawdownTracker()
    for i, pnl in enumerate([50, -20, 10, -30, 80]):
        dd.record(pnl, f"2026-01-05T09:{20+i}:00+05:30")
    assert dd.cumulative_net == 90
    assert dd.peak == 90
    assert dd.max_drawdown_money == 40
    assert len(dd.equity_curve) == 5


def test_equity_curve_records_each_trade():
    dd = DrawdownTracker()
    dd.record(100, "t1")
    dd.record(-50, "t2")
    dd.record(25, "t3")
    assert dd.equity_curve == [("t1", 100.0), ("t2", 50.0), ("t3", 75.0)]


def test_drawdown_pct_against_running_peak():
    """Peak hits 100 then trough hits 40. drawdown_money=60.
    drawdown_pct = 60 / 100 = 60%.
    """
    dd = DrawdownTracker()
    dd.record(100, "t1")
    dd.record(-60, "t2")
    assert dd.max_drawdown_money == 60
    assert dd.max_drawdown_pct == 60.0


def test_drawdown_pct_zero_when_peak_never_positive():
    """Initial capital is the implicit peak (0). Two losses leave us at
    -30, so max_drawdown_money is 30 against the initial baseline.

    But `max_drawdown_pct` divides by the running peak — when the peak
    is 0, we'd divide by zero. The tracker returns 0.0 in that case
    rather than inflate against a tiny denominator or NaN-out the
    summary JSON.
    """
    dd = DrawdownTracker()
    dd.record(-10, "t1")
    dd.record(-20, "t2")
    assert dd.max_drawdown_money == 30
    assert dd.max_drawdown_pct == 0.0  # peak=0 → pct undefined, return 0


def test_drawdown_resets_after_new_peak():
    """A new high resets the trough — only peak-to-trough since the
    most recent peak counts.

    Stream: +100, -30, +60, -20.
    Cumulative: 100, 70, 130, 110.
    Peaks:      100, 100, 130, 130.
    Drawdowns:    0, 30,   0,  20.
    Max drawdown = 30.
    """
    dd = DrawdownTracker()
    for pnl in [100, -30, 60, -20]:
        dd.record(pnl, "t")
    assert dd.max_drawdown_money == 30
