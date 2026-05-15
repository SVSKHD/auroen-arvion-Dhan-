"""Per-symbol strategy params override module-level defaults."""

from datetime import time

import config.strategy_config as cfg
from config.strategy_config import build_strategy_config_for_symbol, money_per_point_for


def test_overrides_win_over_defaults():
    entry = {
        "symbol": "TEST",
        "trigger_dist": 99.0,
        "tp_dist": 88.0,
        "sl_dist": 77.0,
        "lock_step": 11.0,
    }
    config = build_strategy_config_for_symbol(entry)
    assert config.trigger_dist == 99.0
    assert config.tp_dist == 88.0
    assert config.sl_dist == 77.0
    assert config.lock_step == 11.0


def test_missing_overrides_fall_back_to_defaults():
    config = build_strategy_config_for_symbol({"symbol": "BARE"})
    assert config.trigger_dist == cfg.TRIGGER_DIST
    assert config.tp_dist == cfg.TP_DIST
    assert config.sl_dist == cfg.SL_DIST
    assert config.lock_step == cfg.LOCK_STEP


def test_supported_symbols_have_diverging_params():
    """Phase 6 requirement: BANKNIFTY trades larger distances than
    NIFTY because of its volatility profile. This pins that the config
    actually differentiates them, not just nominally.
    """
    nifty = next(s for s in cfg.SUPPORTED_SYMBOLS if s["symbol"] == "NIFTY")
    bn = next(s for s in cfg.SUPPORTED_SYMBOLS if s["symbol"] == "BANKNIFTY")
    mcx = next(s for s in cfg.SUPPORTED_SYMBOLS if s["symbol"] == "MCX_GOLD")
    assert bn["trigger_dist"] > nifty["trigger_dist"]
    assert mcx["trigger_dist"] != nifty["trigger_dist"]


def test_money_per_point_resolver():
    assert money_per_point_for({"money_per_point": 75.0}) == 75.0
    assert money_per_point_for({"lot_size": 15}) == 15.0
    assert money_per_point_for({}) == 1.0
