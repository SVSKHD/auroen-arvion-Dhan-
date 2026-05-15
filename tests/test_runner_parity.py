"""Trade-by-trade equivalence between BacktestEngine and ExecutionEngine PAPER.

Both must route through StrategyRunner, so the same OHLC stream produces the
same trades whether replayed as a backtest or as bars into the PAPER engine.
"""

from datetime import time
from pathlib import Path

import pandas as pd

from backtest.backtest_engine import BacktestEngine
from core.state_store import StateStore
from engine.execution_engine import ExecutionEngine
from engine.strategy_runner import StrategyConfig


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


def _make_csv(tmp_path: Path) -> Path:
    # Two sessions: day 1 a long winner that hits TP; day 2 a short that gets stopped.
    rows = []
    # Day 1: anchor 100. long_entry=120, long_tp=150, long_sl=80.
    rows.append({"time": "2026-01-05 09:15", "open": 100, "high": 100, "low": 100, "close": 100})
    rows.append({"time": "2026-01-05 09:20", "open": 105, "high": 122, "low": 104, "close": 121})  # trigger long at 120
    rows.append({"time": "2026-01-05 09:25", "open": 121, "high": 135, "low": 118, "close": 130})  # trailing only
    rows.append({"time": "2026-01-05 09:30", "open": 130, "high": 151, "low": 128, "close": 149})  # TP at 150
    rows.append({"time": "2026-01-05 09:35", "open": 149, "high": 152, "low": 148, "close": 150})

    # Day 2: anchor 200. short_entry=180, short_tp=150, short_sl=220.
    rows.append({"time": "2026-01-06 09:15", "open": 200, "high": 200, "low": 200, "close": 200})
    rows.append({"time": "2026-01-06 09:20", "open": 195, "high": 198, "low": 179, "close": 181})  # trigger short at 180
    rows.append({"time": "2026-01-06 09:25", "open": 181, "high": 221, "low": 178, "close": 219})  # SL at 220
    rows.append({"time": "2026-01-06 09:30", "open": 219, "high": 222, "low": 215, "close": 218})

    df = pd.DataFrame(rows)
    csv_path = tmp_path / "fixture.csv"
    df.to_csv(csv_path, index=False)
    return csv_path


def test_backtest_and_paper_produce_identical_trades(tmp_path):
    cfg = _config()
    csv_path = _make_csv(tmp_path)

    backtest = BacktestEngine(cfg)
    bt_result = backtest.run(csv_path=str(csv_path), quantity=1, money_per_point=1.0)

    state_store = StateStore.__new__(StateStore)
    state_store.path = tmp_path / "exec_state.json"
    engine = ExecutionEngine(cfg, state_store=state_store, mode="PAPER")

    df = pd.read_csv(csv_path)
    df["time"] = pd.to_datetime(df["time"])
    df["date"] = df["time"].dt.date

    paper_trades = []
    for _, session_df in df.groupby("date"):
        session_df = session_df.sort_values("time").reset_index(drop=True)
        anchor_mask = (
            (session_df["time"].dt.hour == cfg.anchor_time.hour)
            & (session_df["time"].dt.minute == cfg.anchor_time.minute)
        )
        anchor_rows = session_df[anchor_mask]
        if anchor_rows.empty:
            continue
        anchor_idx = anchor_rows.index[0]
        anchor_price = float(anchor_rows.iloc[0]["open"])
        levels = engine.runner.build_levels(anchor_price)

        for i in range(anchor_idx + 1, len(session_df)):
            row = session_df.iloc[i]
            result = engine.on_bar(
                symbol="SYMBOL",
                levels=levels,
                bar_time=row["time"].to_pydatetime(),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                quantity=1,
            )
            if result is not None:
                paper_trades.append(result)
                break  # one trade per session, matching backtest

    assert len(paper_trades) == bt_result["trades"]
    assert len(paper_trades) == 2

    fields = ("side", "entry_price", "exit_price", "points_pnl", "exit_reason", "lock_idx")
    for bt_trade, paper_trade in zip(bt_result["trade_results"], paper_trades):
        for field in fields:
            assert bt_trade[field] == getattr(paper_trade, field), (
                f"Mismatch on {field}: backtest={bt_trade[field]!r} paper={getattr(paper_trade, field)!r}"
            )
        assert bt_trade["entry_time"] == paper_trade.entry_time.isoformat()
        assert bt_trade["exit_time"] == paper_trade.exit_time.isoformat()
