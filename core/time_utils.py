"""IST clock helper.

Every clock read in this codebase must go through `now_ist()` so trading-hour
checks, anchor-capture windows, and audit timestamps are unambiguous across
container hosts and CI runners. Bare `datetime.now()` is timezone-naive and
will silently drift in any environment whose system clock is not Asia/Kolkata.
"""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

NSE_OPEN = time(9, 15)
NSE_CLOSE = time(15, 30)


def now_ist() -> datetime:
    """Current wall-clock as a tz-aware IST datetime."""
    return datetime.now(tz=IST)


def to_ist(dt: datetime) -> datetime:
    """Convert any aware/naive datetime to IST. Naive inputs are assumed UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def in_trading_hours(now: datetime, open_time: time = NSE_OPEN, close_time: time = NSE_CLOSE) -> bool:
    """True when `now` (assumed already IST) is inside [open, close] inclusive of open."""
    t = now.time()
    return open_time <= t <= close_time
