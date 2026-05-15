from datetime import time
from typing import Optional

from engine.strategy_runner import StrategyConfig

MODE = "PAPER"  # PAPER or LIVE

ANCHOR_TIME = "09:15"
SQUARE_OFF_TIME = "15:15"
NO_NEW_TRADE_AFTER = "14:30"
ANCHOR_CAPTURE_WINDOW_SECONDS = 30

# Module-level defaults — used as the fallback when a SUPPORTED_SYMBOLS
# entry omits the matching key inside its `strategy` block.
TRIGGER_DIST = 20.0
TP_DIST = 30.0
SL_DIST = 40.0

LOCK_STEP = 5.0
LOCK_STEPS_COUNT = 6

TICK_SIZE = 0.05

MAX_RISK_PER_TRADE_PCT = 1.0
MAX_DAILY_LOSS = 5000.0
MAX_TRADES_PER_DAY = 3

# R:R note: the defaults TP_DIST=30 / SL_DIST=40 are 0.75:1 — a strategy
# that needs ~57% win rate to break even before costs. The India intraday
# cost model added in Pass A raises the after-cost break-even further.
# Per-symbol overrides below are the place to tune this instrument-by-
# instrument; do not change the module-level defaults without a backtest
# justification.

# Per-symbol entries. Each may carry a nested `strategy` block whose keys
# override the module-level defaults above. `money_per_point` is per
# CONTRACT, `lot_size` is contracts per lot. `instrument_class` is passed
# to the cost model.
SUPPORTED_SYMBOLS = [
    {
        "symbol": "NIFTY",
        "security_id": "NIFTY_PLACEHOLDER",
        "segment": "NSE_FNO",
        "min_capital": 25000,
        "lot_size": 50,
        "money_per_point": 1.0,
        "instrument_class": "EQUITY_INDEX_FUT",
        "strategy": {
            "trigger_dist": 20.0,
            "tp_dist": 30.0,
            "sl_dist": 40.0,
            "lock_step": 5.0,
            "lock_steps_count": 6,
        },
    },
    {
        "symbol": "BANKNIFTY",
        "security_id": "BANKNIFTY_PLACEHOLDER",
        "segment": "NSE_FNO",
        "min_capital": 50000,
        "lot_size": 15,
        "money_per_point": 1.0,
        "instrument_class": "EQUITY_INDEX_FUT",
        "strategy": {
            "trigger_dist": 50.0,
            "tp_dist": 75.0,
            "sl_dist": 100.0,
            "lock_step": 15.0,
            "lock_steps_count": 6,
        },
    },
    {
        "symbol": "MCX_GOLD",
        "security_id": "MCX_GOLD_PLACEHOLDER",
        "segment": "MCX_COMM",
        "min_capital": 100000,
        "lot_size": 100,
        "money_per_point": 1.0,
        "instrument_class": "MCX_COMM_FUT",
        "strategy": {
            "trigger_dist": 40.0,
            "tp_dist": 60.0,
            "sl_dist": 80.0,
            "lock_step": 10.0,
            "lock_steps_count": 6,
        },
    },
]


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def build_strategy_config(symbol_entry: Optional[dict] = None) -> StrategyConfig:
    """Build a StrategyConfig from a SUPPORTED_SYMBOLS entry.

    Per-symbol values in the entry's `strategy` block win over the
    module-level defaults. Time and tick-size fields always come from
    the module-level config — they are session-wide, not per-symbol.

    Passing `None` (or an entry without a `strategy` block) returns the
    module-level defaults verbatim.
    """
    strategy_block: dict = (symbol_entry or {}).get("strategy", {}) or {}
    return StrategyConfig(
        trigger_dist=float(strategy_block.get("trigger_dist", TRIGGER_DIST)),
        tp_dist=float(strategy_block.get("tp_dist", TP_DIST)),
        sl_dist=float(strategy_block.get("sl_dist", SL_DIST)),
        lock_step=float(strategy_block.get("lock_step", LOCK_STEP)),
        lock_steps_count=int(strategy_block.get("lock_steps_count", LOCK_STEPS_COUNT)),
        anchor_time=_parse_hhmm(ANCHOR_TIME),
        square_off_time=_parse_hhmm(SQUARE_OFF_TIME),
        tick_size=TICK_SIZE,
    )


def build_default_strategy_config() -> StrategyConfig:
    """Back-compat shim used by run_dhan.py and other Phase 5 callers."""
    return build_strategy_config(None)
