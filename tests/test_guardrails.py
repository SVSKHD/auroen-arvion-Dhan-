"""GuardRails must block trade N+1 when max_trades_per_day=N."""

from datetime import datetime, time

from core.state_store import StateStore
from engine.execution_engine import ExecutionEngine
from engine.strategy_runner import StrategyConfig
from risk.guardrails import GuardRails


def _config() -> StrategyConfig:
    return StrategyConfig(
        trigger_dist=10.0,
        tp_dist=20.0,
        sl_dist=20.0,
        lock_step=5.0,
        lock_steps_count=4,
        anchor_time=time(9, 15),
        square_off_time=time(15, 15),
        tick_size=0.05,
    )


def _state_store(tmp_path, name):
    store = StateStore.__new__(StateStore)
    store.path = tmp_path / name
    return store


def test_trade_count_rejects_trade_n_plus_one(tmp_path):
    cfg = _config()
    max_trades = 2
    guards = GuardRails(max_daily_loss=10_000.0, max_trades_per_day=max_trades)
    engine = ExecutionEngine(
        cfg,
        guardrails=guards,
        state_store=_state_store(tmp_path, "exec.json"),
        mode="PAPER",
    )

    levels = engine.runner.build_levels(anchor_price=100.0)
    # long_entry=110, long_tp=130, long_sl=90

    # Each "session" runs entry then immediate TP to close, on the same day.
    base_day = datetime(2026, 3, 4)

    def run_round_trip(offset_minutes: int) -> bool:
        entry_time = base_day.replace(hour=9, minute=20 + offset_minutes * 2)
        exit_time = base_day.replace(hour=9, minute=21 + offset_minutes * 2)
        engine.on_bar("SYM", levels, entry_time, high=115, low=99, close=112, quantity=1)
        before_close = "SYM" in engine.positions
        if before_close:
            engine.on_bar("SYM", levels, exit_time, high=135, low=110, close=130, quantity=1)
        return before_close

    for i in range(max_trades):
        opened = run_round_trip(i)
        assert opened, f"Trade {i + 1} must be allowed"

    assert engine.day_trades == max_trades

    # Now N+1 attempt: there must be NO open position created.
    attempt_time = base_day.replace(hour=10, minute=0)
    engine.on_bar("SYM", levels, attempt_time, high=115, low=99, close=112, quantity=1)
    assert "SYM" not in engine.positions
    assert engine.day_trades == max_trades
    assert any(r["reason"] == "MAX_TRADES_REACHED" for r in engine.rejections)


def test_daily_loss_rejects_new_entries(tmp_path):
    cfg = _config()
    guards = GuardRails(max_daily_loss=500.0, max_trades_per_day=10)
    engine = ExecutionEngine(
        cfg,
        guardrails=guards,
        state_store=_state_store(tmp_path, "exec.json"),
        mode="PAPER",
    )
    engine._roll_day_if_needed(datetime(2026, 3, 4).date())
    engine.day_pnl = -600.0  # already past loss limit

    levels = engine.runner.build_levels(anchor_price=100.0)
    engine.on_bar(
        "SYM",
        levels,
        datetime(2026, 3, 4, 9, 30),
        high=115,
        low=99,
        close=112,
        quantity=1,
    )
    assert "SYM" not in engine.positions
    assert any(r["reason"] == "MAX_DAILY_LOSS_HIT" for r in engine.rejections)


def test_paper_default_when_mode_missing(tmp_path):
    engine = ExecutionEngine(
        _config(),
        state_store=_state_store(tmp_path, "exec.json"),
        mode=None,
    )
    assert engine.mode == "PAPER"


def test_live_requires_broker(tmp_path):
    import pytest

    with pytest.raises(RuntimeError):
        ExecutionEngine(
            _config(),
            state_store=_state_store(tmp_path, "exec.json"),
            mode="LIVE",
        )
