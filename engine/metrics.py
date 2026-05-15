"""Drawdown + equity-curve tracking.

Both `engine/audit.py` (live + paper sessions) and
`backtest/backtest_engine.py` (offline backtests) need to maintain the
same running statistics: cumulative net PnL after each trade, the peak
seen so far, and the maximum peak-to-trough drawdown observed. Pass A
factors this into a single tracker so the two paths cannot drift.

`record(net_pnl, exit_ts)` is the only mutator. The tracker assumes
each `net_pnl` is the realised PnL of one closed trade (positive for
wins, negative for losses). Equity-curve entries are `(exit_ts_iso,
cumulative_net)`.

Drawdown convention:
  - Drawdown is measured against the running peak (highest cumulative
    net PnL seen so far). The first trade establishes the initial peak.
  - `max_drawdown_money` is non-negative.
  - `max_drawdown_pct` = max_drawdown_money / max_equity_seen × 100,
    where `max_equity_seen` is the all-time peak. Returns 0.0 when the
    peak never went above 0 (i.e. we're net-negative the whole time and
    "drawdown vs peak" is meaningless).
"""

from dataclasses import dataclass, field


@dataclass
class DrawdownTracker:
    cumulative_net: float = 0.0
    peak: float = 0.0
    max_drawdown_money: float = 0.0
    equity_curve: list[tuple[str, float]] = field(default_factory=list)

    def record(self, net_pnl: float, exit_ts_iso: str) -> None:
        self.cumulative_net = round(self.cumulative_net + net_pnl, 2)
        if self.cumulative_net > self.peak:
            self.peak = self.cumulative_net
        drawdown = self.peak - self.cumulative_net
        if drawdown > self.max_drawdown_money:
            self.max_drawdown_money = round(drawdown, 2)
        self.equity_curve.append((exit_ts_iso, self.cumulative_net))

    @property
    def max_drawdown_pct(self) -> float:
        if self.peak <= 0:
            return 0.0
        return round((self.max_drawdown_money / self.peak) * 100.0, 2)
