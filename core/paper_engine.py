from strategy.levels import build_order_levels


class PaperEngine:
    def simulate(
        self,
        symbol: str,
        anchor_price: float,
        current_price: float,
        trigger_dist: float,
        tp_dist: float,
        sl_dist: float,
    ) -> dict:
        levels = build_order_levels(
            anchor_price=anchor_price,
            trigger_dist=trigger_dist,
            tp_dist=tp_dist,
            sl_dist=sl_dist,
        )

        direction = "NONE"
        pnl = 0.0

        if current_price >= levels.long_entry:
            direction = "LONG"
            pnl = current_price - levels.long_entry

        elif current_price <= levels.short_entry:
            direction = "SHORT"
            pnl = levels.short_entry - current_price

        return {
            "symbol": symbol,
            "direction": direction,
            "paper_pnl": round(pnl, 2),
            "levels": levels.__dict__,
        }
