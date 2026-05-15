from datetime import time

from engine.strategy_runner import StrategyConfig

MODE = "PAPER"  # PAPER or LIVE

ANCHOR_TIME = "09:15"
SQUARE_OFF_TIME = "15:15"
NO_NEW_TRADE_AFTER = "14:30"
ANCHOR_CAPTURE_WINDOW_SECONDS = 30

# Defaults — each SUPPORTED_SYMBOLS entry may override any of these per-symbol.
TRIGGER_DIST = 20.0
TP_DIST = 30.0
SL_DIST = 40.0

LOCK_STEP = 5.0
LOCK_STEPS_COUNT = 6

TICK_SIZE = 0.05

MAX_RISK_PER_TRADE_PCT = 1.0
MAX_DAILY_LOSS = 5000.0
MAX_TRADES_PER_DAY = 3

# R:R warning: TP_DIST=30 vs SL_DIST=40 is 0.75:1 — needs ~57% win rate to
# break even before costs. Phase 6 surfaces this in the PR description; do
# not change defaults without a backtest justification.

# Per-symbol overrides for trigger_dist, tp_dist, sl_dist, lock_step,
# money_per_point. Anything absent falls back to the module-level default
# above.
SUPPORTED_SYMBOLS = [
    {
        "symbol": "NIFTY",
        "security_id": "NIFTY_PLACEHOLDER",
        "segment": "NSE_FNO",
        "min_capital": 25000,
        "lot_size": 50,
        "trigger_dist": 20.0,
        "tp_dist": 30.0,
        "sl_dist": 40.0,
        "lock_step": 5.0,
        "money_per_point": 50.0,  # 1 point = lot_size INR per contract
        "instrument_class": "EQUITY_INDEX_FUT",
    },
    {
        "symbol": "BANKNIFTY",
        "security_id": "BANKNIFTY_PLACEHOLDER",
        "segment": "NSE_FNO",
        "min_capital": 50000,
        "lot_size": 15,
        "trigger_dist": 50.0,
        "tp_dist": 75.0,
        "sl_dist": 100.0,
        "lock_step": 15.0,
        "money_per_point": 15.0,
        "instrument_class": "EQUITY_INDEX_FUT",
    },
    {
        "symbol": "MCX_GOLD",
        "security_id": "MCX_GOLD_PLACEHOLDER",
        "segment": "MCX_COMM",
        "min_capital": 100000,
        "lot_size": 100,  # MCX_GOLD minimum lot grams
        "trigger_dist": 40.0,
        "tp_dist": 60.0,
        "sl_dist": 80.0,
        "lock_step": 10.0,
        "money_per_point": 100.0,
        "instrument_class": "MCX_COMM_FUT",
    },
]


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def build_default_strategy_config() -> StrategyConfig:
    return StrategyConfig(
        trigger_dist=TRIGGER_DIST,
        tp_dist=TP_DIST,
        sl_dist=SL_DIST,
        lock_step=LOCK_STEP,
        lock_steps_count=LOCK_STEPS_COUNT,
        anchor_time=_parse_hhmm(ANCHOR_TIME),
        square_off_time=_parse_hhmm(SQUARE_OFF_TIME),
        tick_size=TICK_SIZE,
    )


def build_strategy_config_for_symbol(symbol_entry: dict) -> StrategyConfig:
    """Merge module-level defaults with per-symbol overrides into a fresh
    StrategyConfig. Per-symbol values win when present.
    """
    return StrategyConfig(
        trigger_dist=float(symbol_entry.get("trigger_dist", TRIGGER_DIST)),
        tp_dist=float(symbol_entry.get("tp_dist", TP_DIST)),
        sl_dist=float(symbol_entry.get("sl_dist", SL_DIST)),
        lock_step=float(symbol_entry.get("lock_step", LOCK_STEP)),
        lock_steps_count=int(symbol_entry.get("lock_steps_count", LOCK_STEPS_COUNT)),
        anchor_time=_parse_hhmm(ANCHOR_TIME),
        square_off_time=_parse_hhmm(SQUARE_OFF_TIME),
        tick_size=float(symbol_entry.get("tick_size", TICK_SIZE)),
    )


def money_per_point_for(symbol_entry: dict) -> float:
    return float(symbol_entry.get("money_per_point", symbol_entry.get("lot_size", 1)))
