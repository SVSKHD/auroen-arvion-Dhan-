from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from engine.strategy_runner import (
    M1Bar,
    StrategyConfig,
    StrategyPosition,
    StrategyRunner,
    StrategyTradeResult,
)


@dataclass
class BacktestSummary:
    trades: int
    wins: int
    losses: int
    win_rate: float
    total_points_pnl: float
    total_money_pnl: float
    profitable: bool
    trade_results: list[dict]


class BacktestEngine:
    """Session-based anchor breakout backtest.

    Drives StrategyRunner over OHLC bars grouped by IST trading day. One trade
    per session (first threshold breach). All entry/TP/SL/trailing/square-off
    decisions come from StrategyRunner — no logic is duplicated here.
    """

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.runner = StrategyRunner(config)

    def run(
        self,
        csv_path: str,
        quantity: int = 1,
        money_per_point: float = 1.0,
        m1_csv_path: Optional[str] = None,
    ) -> dict:
        df = self._read_bars(csv_path)
        m1_df = self._read_bars(m1_csv_path) if m1_csv_path else None

        trades: list[StrategyTradeResult] = []

        for date_key, session_df in df.groupby("date"):
            session_df = session_df.sort_values("time").reset_index(drop=True)
            m1_session = None
            if m1_df is not None:
                m1_session = m1_df[m1_df["date"] == date_key].sort_values("time").reset_index(drop=True)
            trade = self._run_session(session_df, m1_session, quantity, money_per_point)
            if trade is not None:
                trades.append(trade)

        return self._summarize(trades, money_per_point)

    @staticmethod
    def _read_bars(csv_path: str) -> pd.DataFrame:
        df = pd.read_csv(csv_path)
        required = {"time", "open", "high", "low", "close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"CSV missing columns: {sorted(missing)}")
        df["time"] = pd.to_datetime(df["time"])
        df["date"] = df["time"].dt.date
        return df

    def _run_session(
        self,
        session_df: pd.DataFrame,
        m1_session: Optional[pd.DataFrame],
        quantity: int,
        money_per_point: float,
    ) -> Optional[StrategyTradeResult]:
        anchor_time = self.config.anchor_time
        anchor_mask = (
            (session_df["time"].dt.hour == anchor_time.hour)
            & (session_df["time"].dt.minute == anchor_time.minute)
        )
        anchor_rows = session_df[anchor_mask]
        if anchor_rows.empty:
            return None

        anchor_idx = anchor_rows.index[0]
        anchor_price = float(anchor_rows.iloc[0]["open"])
        levels = self.runner.build_levels(anchor_price)

        position: Optional[StrategyPosition] = None

        for i in range(anchor_idx + 1, len(session_df)):
            row = session_df.iloc[i]
            bar_time: datetime = row["time"].to_pydatetime()
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])

            if position is None:
                position = self.runner.detect_entry(
                    symbol=str(row.get("symbol", "SYMBOL")),
                    levels=levels,
                    bar_time=bar_time,
                    high=high,
                    low=low,
                    quantity=quantity,
                )
                continue

            result = self._evaluate_bar(position, m1_session, bar_time, high, low, close)
            if result is not None:
                return self._apply_money(result, money_per_point)

        return None

    def _evaluate_bar(
        self,
        position: StrategyPosition,
        m1_session: Optional[pd.DataFrame],
        bar_time: datetime,
        high: float,
        low: float,
        close: float,
    ) -> Optional[StrategyTradeResult]:
        if self.config.bar_resolution == "M1" and m1_session is not None:
            sub_bars = self._m1_sub_bars(m1_session, bar_time)
            if sub_bars:
                return self.runner.replay_intra_bar(position, sub_bars)
        return self.runner.evaluate_exit(
            position=position,
            bar_time=bar_time,
            high=high,
            low=low,
            close=close,
        )

    @staticmethod
    def _m1_sub_bars(m1_session: pd.DataFrame, m5_bar_time: datetime) -> list[M1Bar]:
        window_start = m5_bar_time
        window_end = m5_bar_time + timedelta(minutes=5)
        mask = (m1_session["time"] >= window_start) & (m1_session["time"] < window_end)
        rows = m1_session[mask]
        return [
            M1Bar(
                time=row["time"].to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
            for _, row in rows.iterrows()
        ]

    @staticmethod
    def _apply_money(
        result: StrategyTradeResult,
        money_per_point: float,
    ) -> StrategyTradeResult:
        result.money_pnl = round(result.points_pnl * result.quantity * money_per_point, 2)
        return result

    @staticmethod
    def _summarize(
        trades: list[StrategyTradeResult],
        money_per_point: float,
    ) -> dict:
        wins = sum(1 for t in trades if t.points_pnl > 0)
        losses = sum(1 for t in trades if t.points_pnl <= 0)
        total_points = round(sum(t.points_pnl for t in trades), 2)
        total_money = round(sum(t.money_pnl for t in trades), 2)
        win_rate = round((wins / len(trades)) * 100, 2) if trades else 0.0

        return {
            "trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_points_pnl": total_points,
            "total_money_pnl": total_money,
            "money_per_point": money_per_point,
            "profitable": total_money > 0,
            "trade_results": [StrategyRunner.serialize_trade(t) for t in trades],
        }
