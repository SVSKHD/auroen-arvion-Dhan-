"""Broker-neutral trailing lock logic."""

import math
from typing import Optional

LOCK_STEP = 5.0
LOCK_STEPS_COUNT = 6
EPSILON = 1e-9


def calculate_lock_step(favorable_move: float) -> int:
    if favorable_move + EPSILON < LOCK_STEP:
        return -1

    steps_reached = math.floor((favorable_move + EPSILON) / LOCK_STEP)
    idx = steps_reached - 1

    return min(idx, LOCK_STEPS_COUNT - 1)


def calculate_new_sl(entry: float, side: str, lock_step_idx: int) -> Optional[float]:
    if lock_step_idx < 0:
        return None

    lock_offset = LOCK_STEP * (lock_step_idx + 1)

    if side == "LONG":
        return entry + lock_offset

    if side == "SHORT":
        return entry - lock_offset

    return None


def should_update_sl(current_idx: int, new_idx: int) -> bool:
    return new_idx > current_idx
