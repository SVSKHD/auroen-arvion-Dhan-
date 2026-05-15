"""Per-symbol strategy params override module-level defaults.

The Pass A brief defines a nested `strategy` block per SUPPORTED_SYMBOLS
entry. Missing keys fall back to the module-level constants.
"""

import config.strategy_config as cfg
from config.strategy_config import build_strategy_config


def test_per_symbol_strategy_block_wins_over_defaults():
    nifty = next(s for s in cfg.SUPPORTED_SYMBOLS if s["symbol"] == "NIFTY")
    config = build_strategy_config(nifty)
    block = nifty["strategy"]
    assert config.trigger_dist == block["trigger_dist"]
    assert config.tp_dist == block["tp_dist"]
    assert config.sl_dist == block["sl_dist"]
    assert config.lock_step == block["lock_step"]
    assert config.lock_steps_count == block["lock_steps_count"]


def test_no_strategy_block_falls_back_to_defaults():
    """A symbol entry that lacks the `strategy` key returns the
    module-level defaults verbatim.
    """
    config = build_strategy_config({"symbol": "RAW", "lot_size": 1})
    assert config.trigger_dist == cfg.TRIGGER_DIST
    assert config.tp_dist == cfg.TP_DIST
    assert config.sl_dist == cfg.SL_DIST
    assert config.lock_step == cfg.LOCK_STEP


def test_partial_override_keeps_other_defaults():
    """Only `tp_dist` is overridden; the rest of the block falls back."""
    config = build_strategy_config({"strategy": {"tp_dist": 99.0}})
    assert config.tp_dist == 99.0
    assert config.trigger_dist == cfg.TRIGGER_DIST
    assert config.sl_dist == cfg.SL_DIST
    assert config.lock_step == cfg.LOCK_STEP


def test_passing_none_returns_defaults():
    """The legacy `build_default_strategy_config()` shim delegates here."""
    from config.strategy_config import build_default_strategy_config
    a = build_strategy_config(None)
    b = build_default_strategy_config()
    assert a.trigger_dist == b.trigger_dist == cfg.TRIGGER_DIST
    assert a.sl_dist == b.sl_dist == cfg.SL_DIST


def test_banknifty_and_mcx_diverge_from_nifty():
    """The Pass A spec wants per-symbol params that actually differ from
    one another, not just nominal copies of the defaults.
    """
    n = next(s for s in cfg.SUPPORTED_SYMBOLS if s["symbol"] == "NIFTY")
    bn = next(s for s in cfg.SUPPORTED_SYMBOLS if s["symbol"] == "BANKNIFTY")
    mcx = next(s for s in cfg.SUPPORTED_SYMBOLS if s["symbol"] == "MCX_GOLD")
    assert bn["strategy"]["trigger_dist"] != n["strategy"]["trigger_dist"]
    assert mcx["strategy"]["trigger_dist"] != n["strategy"]["trigger_dist"]


def test_anchor_and_squareoff_always_from_module_level():
    """Session-wide settings stay at module level even when the per-symbol
    block has overrides — the brief says these are session-wide.
    """
    config = build_strategy_config({"strategy": {"trigger_dist": 99.0}})
    from config.strategy_config import _parse_hhmm
    assert config.anchor_time == _parse_hhmm(cfg.ANCHOR_TIME)
    assert config.square_off_time == _parse_hhmm(cfg.SQUARE_OFF_TIME)
    assert config.tick_size == cfg.TICK_SIZE
