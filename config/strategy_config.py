from datetime import time

from engine.strategy_runner import StrategyConfig

MODE = "PAPER"  # PAPER or LIVE

ANCHOR_TIME = "09:15"
SQUARE_OFF_TIME = "15:15"
NO_NEW_TRADE_AFTER = "14:30"
ANCHOR_CAPTURE_WINDOW_SECONDS = 30

TRIGGER_DIST = 20.0
TP_DIST = 30.0
SL_DIST = 40.0

LOCK_STEP = 5.0
LOCK_STEPS_COUNT = 6

TICK_SIZE = 0.05

MAX_RISK_PER_TRADE_PCT = 1.0
MAX_DAILY_LOSS = 5000.0
MAX_TRADES_PER_DAY = 3

SUPPORTED_SYMBOLS = [
    {
        "symbol": "NIFTY",
        "security_id": "NIFTY_PLACEHOLDER",
        "segment": "NSE_FNO",
        "min_capital": 25000,
        "lot_size": 50,
    },
    {
        "symbol": "BANKNIFTY",
        "security_id": "BANKNIFTY_PLACEHOLDER",
        "segment": "NSE_FNO",
        "min_capital": 50000,
        "lot_size": 15,
    },
    {
        "symbol": "MCX_GOLD",
        "security_id": "MCX_GOLD_PLACEHOLDER",
        "segment": "MCX_COMM",
        "min_capital": 100000,
        "lot_size": 1,
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
