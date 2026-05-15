from dataclasses import dataclass
from datetime import time

import pandas as pd

from strategy.levels import build_order_levels


@dataclass
class TradeResult:
    side: str
    entry_price: float
    exit_price: float
    pnl: float
    exit_reason: str


class BacktestEngine:
    """Session-based anchor breakout backtest.

    Rules:
    - 09:15 candle open becomes anchor
    - only candles AFTER anchor are eligible for entry
    - first threshold breach decides side
    - trade then follows TP / SL / square-off logic
    - one trade per session/day
    """

    def run(
        self,
        csv_path: str,
        trigger_dist: float,
        tp_dist: float,
        sl_dist: float,
        anchor_hour: int = 9,
        anchor_minute: int = 15,
        square_off_hour: int = 15,
        square_off_minute: int = 15,
    ) -> dict:
        df = pd.read_csv(csv_path)

        if "time" not in df.columns:
            raise ValueError("CSV must contain 'time' column")

        df["time"] = pd.to_datetime(df["time"])
        df["date"] = df["time"].dt.date

        all_trades: list[TradeResult] = []

        grouped = df.groupby("date")

        for _, session_df in grouped:
            session_df = session_df.sort_values("time").reset_index(drop=True)

            anchor_row = session_df[
                (session_df["time"].dt.hour == anchor_hour)
                & (session_df["time"].dt.minute == anchor_minute)
            ]

            if anchor_row.empty:
                continue

            anchor_idx = anchor_row.index[0]
            anchor_price = float(anchor_row.iloc[0]["open"])

            levels = build_order_levels(
                anchor_price=anchor_price,
                trigger_dist=trigger_dist,
                tp_dist=tp_dist,
                sl_dist=sl_dist,
            )

            trade: TradeResult | None = None

            in_position = False
            side = None
            entry_price = None
            tp_price = None
            sl_price = None

            for i in range(anchor_idx + 1, len(session_df)):
                row = session_df.iloc[i]

                high = float(row["high"])
                low = float(row["low"])
                close = float(row["close"])

                current_time = row["time"].time()

                if not in_position:
                    if high >= levels.long_entry:
                        in_position = True
                        side = "LONG"
                        entry_price = levels.long_entry
                        tp_price = levels.long_tp
                        sl_price = levels.long_sl

                    elif low <= levels.short_entry:
                        in_position = True
                        side = "SHORT"
                        entry_price = levels.short_entry
                        tp_price = levels.short_tp
                        sl_price = levels.short_sl

                    continue

                if side == "LONG":
                    sl_hit = low <= sl_price
                    tp_hit = high >= tp_price

                    if sl_hit and tp_hit:
                        exit_price = sl_price
                        pnl = exit_price - entry_price

                        trade = TradeResult(
                            side=side,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl=pnl,
                            exit_reason="SL_FIRST",
                        )
                        break

                    if sl_hit:
                        exit_price = sl_price
                        pnl = exit_price - entry_price

                        trade = TradeResult(
                            side=side,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl=pnl,
                            exit_reason="SL",
                        )
                        break

                    if tp_hit:
                        exit_price = tp_price
                        pnl = exit_price - entry_price

                        trade = TradeResult(
                            side=side,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl=pnl,
                            exit_reason="TP",
                        )
                        break

                elif side == "SHORT":
                    sl_hit = high >= sl_price
                    tp_hit = low <= tp_price

                    if sl_hit and tp_hit:
                        exit_price = sl_price
                        pnl = entry_price - exit_price

                        trade = TradeResult(
                            side=side,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl=pnl,
                            exit_reason="SL_FIRST",
                        )
                        break

                    if sl_hit:
                        exit_price = sl_price
                        pnl = entry_price - exit_price

                        trade = TradeResult(
                            side=side,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl=pnl,
                            exit_reason="SL",
                        )
                        break

                    if tp_hit:
                        exit_price = tp_price
                        pnl = entry_price - exit_price

                        trade = TradeResult(
                            side=side,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl=pnl,
                            exit_reason="TP",
                        )
                        break

                if current_time >= time(square_off_hour, square_off_minute):
                    if side == "LONG":
                        pnl = close - entry_price
                    else:
                        pnl = entry_price - close

                    trade = TradeResult(
                        side=side,
                        entry_price=entry_price,
                        exit_price=close,
                        pnl=pnl,
                        exit_reason="SQUARE_OFF",
                    )
                    break

            if trade:
                all_trades.append(trade)

        total_pnl = round(sum(t.pnl for t in all_trades), 2)
        wins = len([t for t in all_trades if t.pnl > 0])
        losses = len([t for t in all_trades if t.pnl <= 0])

        return {
            "trades": len(all_trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / len(all_trades)) * 100, 2)
            if all_trades
            else 0.0,
            "total_pnl": total_pnl,
            "profitable": total_pnl > 0,
            "trade_results": [t.__dict__ for t in all_trades],
        }
