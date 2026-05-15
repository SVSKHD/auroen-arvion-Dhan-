from dataclasses import dataclass


@dataclass
class OrderLevels:
    anchor: float
    long_entry: float
    long_sl: float
    long_tp: float
    short_entry: float
    short_sl: float
    short_tp: float


DEFAULT_TICK_SIZE = 0.05


def round_to_tick(price: float, tick_size: float = DEFAULT_TICK_SIZE) -> float:
    return round(round(price / tick_size) * tick_size, 2)


def build_order_levels(
    anchor_price: float,
    trigger_dist: float,
    tp_dist: float,
    sl_dist: float,
    tick_size: float = DEFAULT_TICK_SIZE,
) -> OrderLevels:
    long_entry = round_to_tick(anchor_price + trigger_dist, tick_size)
    long_sl = round_to_tick(long_entry - sl_dist, tick_size)
    long_tp = round_to_tick(long_entry + tp_dist, tick_size)

    short_entry = round_to_tick(anchor_price - trigger_dist, tick_size)
    short_sl = round_to_tick(short_entry + sl_dist, tick_size)
    short_tp = round_to_tick(short_entry - tp_dist, tick_size)

    return OrderLevels(
        anchor=round_to_tick(anchor_price, tick_size),
        long_entry=long_entry,
        long_sl=long_sl,
        long_tp=long_tp,
        short_entry=short_entry,
        short_sl=short_sl,
        short_tp=short_tp,
    )
