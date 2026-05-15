import pandas as pd

from strategy.levels import build_order_levels


class BacktestEngine:
    def run(
        self,
        csv_path: str,
        trigger_dist: float,
        tp_dist: float,
        sl_dist: float,
    ) -> dict:
        df = pd.read_csv(csv_path)

        total_pnl = 0.0
        trades = 0

        for _, row in df.iterrows():
            anchor = row["open"]
            high = row["high"]
            low = row["low"]

            levels = build_order_levels(
                anchor_price=anchor,
                trigger_dist=trigger_dist,
                tp_dist=tp_dist,
                sl_dist=sl_dist,
            )

            if high >= levels.long_entry:
                pnl = min(high - levels.long_entry, tp_dist)
                total_pnl += pnl
                trades += 1

            elif low <= levels.short_entry:
                pnl = min(levels.short_entry - low, tp_dist)
                total_pnl += pnl
                trades += 1

        return {
            "trades": trades,
            "total_pnl": round(total_pnl, 2),
            "profitable": total_pnl > 0,
        }
