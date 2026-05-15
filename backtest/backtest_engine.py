from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

from costs.india_intraday import CostBreakdown, CostRates, round_trip_cost
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
        instrument_class: Optional[str] = None,
        cost_rates: Optional[CostRates] = None,
    ) -> dict:
        df = self._load_ohlc(csv_path)
        m1_df: Optional[pd.DataFrame] = (
            self._load_ohlc(m1_csv_path) if m1_csv_path is not None else None
        )

        trades: list[StrategyTradeResult] = []
        cost_breakdowns: list[CostBreakdown] = []

        for session_date, session_df in df.groupby("date"):
            session_df = session_df.sort_values("time").reset_index(drop=True)
            session_m1 = (
                m1_df[m1_df["date"] == session_date].sort_values("time").reset_index(drop=True)
                if m1_df is not None
                else None
            )
            trade = self._run_session(session_df, session_m1, quantity, money_per_point)
            if trade is not None:
                trades.append(trade)
                if instrument_class is not None:
                    cost_breakdowns.append(round_trip_cost(
                        entry_price=trade.entry_price,
                        exit_price=trade.exit_price,
                        quantity=trade.quantity,
                        instrument_class=instrument_class,
                        rates=cost_rates,
                    ))
                else:
                    cost_breakdowns.append(CostBreakdown())

        return self._summarize(trades, money_per_point, cost_breakdowns)

    @staticmethod
    def _load_ohlc(csv_path: str) -> pd.DataFrame:
        df = pd.read_csv(csv_path)
        if "time" not in df.columns:
            raise ValueError("CSV must contain 'time' column")
        required_cols = {"time", "open", "high", "low", "close"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"CSV missing columns: {sorted(missing)}")
        df["time"] = pd.to_datetime(df["time"])
        df["date"] = df["time"].dt.date
        return df

    def _run_session(
        self,
        session_df: pd.DataFrame,
        session_m1: Optional[pd.DataFrame],
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
        entry_bar_time: Optional[datetime] = None

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
                if position is not None:
                    entry_bar_time = bar_time
                continue

            if session_m1 is not None:
                m1_bars = self._m1_window(session_m1, bar_time)
                result = self.runner.replay_intra_bar(position, m1_bars)
            else:
                result = self.runner.evaluate_exit(
                    position=position,
                    bar_time=bar_time,
                    high=high,
                    low=low,
                    close=close,
                )
            if result is not None:
                return self._apply_money(result, money_per_point)

        _ = entry_bar_time
        return None

    @staticmethod
    def _m1_window(session_m1: pd.DataFrame, m5_bar_end: datetime) -> list[M1Bar]:
        """Return the M1 bars whose timestamps fall inside the M5 window
        ending at `m5_bar_end`. Convention: an M5 bar timestamped 09:25
        covers M1 bars 09:20..09:24.
        """
        window_start = m5_bar_end - pd.Timedelta(minutes=5)
        mask = (session_m1["time"] > window_start) & (session_m1["time"] <= m5_bar_end)
        rows = session_m1[mask]
        return [
            M1Bar(
                bar_time=r["time"].to_pydatetime(),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
            )
            for _, r in rows.iterrows()
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
        cost_breakdowns: Optional[list[CostBreakdown]] = None,
    ) -> dict:
        wins = sum(1 for t in trades if t.points_pnl > 0)
        losses = sum(1 for t in trades if t.points_pnl <= 0)
        total_points = round(sum(t.points_pnl for t in trades), 2)
        gross_money = round(sum(t.money_pnl for t in trades), 2)
        win_rate = round((wins / len(trades)) * 100, 2) if trades else 0.0

        cost_breakdowns = cost_breakdowns or [CostBreakdown() for _ in trades]
        total_costs = round(sum(c.total for c in cost_breakdowns), 2)
        net_money = round(gross_money - total_costs, 2)

        # Equity curve: cumulative net PnL after each trade.
        equity_curve: list[tuple[str, float]] = []
        cumulative_net = 0.0
        max_drawdown = 0.0
        peak = 0.0
        for t, c in zip(trades, cost_breakdowns):
            cumulative_net += t.money_pnl - c.total
            equity_curve.append((t.exit_time.isoformat(), round(cumulative_net, 2)))
            if cumulative_net > peak:
                peak = cumulative_net
            drawdown = peak - cumulative_net
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        max_drawdown_pct = (
            round((max_drawdown / peak) * 100, 2) if peak > 0 else 0.0
        )

        trade_records = []
        for t, c in zip(trades, cost_breakdowns):
            record = StrategyRunner.serialize_trade(t)
            record["cost_breakdown"] = c.as_dict()
            record["net_money_pnl"] = round(t.money_pnl - c.total, 2)
            trade_records.append(record)

        return {
            "trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_points_pnl": total_points,
            "gross_pnl": gross_money,
            "net_pnl": net_money,
            "total_costs": total_costs,
            # Pre-Phase-6 callers consume `total_money_pnl`; preserve it
            # so the parity test and the CLI summary keep working.
            "total_money_pnl": gross_money,
            "money_per_point": money_per_point,
            "profitable": net_money > 0,
            "max_drawdown_money": round(max_drawdown, 2),
            "max_drawdown_pct": max_drawdown_pct,
            "equity_curve": equity_curve,
            "trade_results": trade_records,
        }
