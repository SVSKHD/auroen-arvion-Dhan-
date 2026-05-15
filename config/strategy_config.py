MODE = "PAPER"  # PAPER or LIVE

ANCHOR_TIME = "09:20"
SQUARE_OFF_TIME = "15:15"

TRIGGER_DIST = 20.0
TP_DIST = 30.0
SL_DIST = 40.0

LOCK_STEP = 5.0
LOCK_STEPS_COUNT = 6

MAX_RISK_PER_TRADE_PCT = 1.0

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
