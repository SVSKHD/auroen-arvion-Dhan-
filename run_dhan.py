"""Aureon Arvion Dhan — entry point.

Run one of three modes:

    python run_dhan.py --mode=paper       (default)
    python run_dhan.py --mode=backtest --csv=data/NIFTY_M5.csv
    python run_dhan.py --mode=live        (refused unless explicit, see below)

LIVE refusal: per the Phase 5 brief, MODE=LIVE requires BOTH:
    1. `config.MODE == "LIVE"` in config/strategy_config.py
    2. environment variable MODE=LIVE
If either is missing, the process aborts with a non-zero exit. This is
intentional — accidentally executing a real-money path is the most
expensive bug we can ship.

Graceful shutdown: SIGTERM and SIGINT call `Orchestrator.shutdown()`,
which squares off open positions + cancels pending orders (LIVE) and
always flushes the daily audit triplet to `state/audit/<date>/`.
"""

import argparse
import os
import signal
import sys
import time as time_module
from datetime import time
from typing import Optional

import config.strategy_config as cfg
from backtest.backtest_engine import BacktestEngine
from brokers.paper.paper_broker import PaperBroker
from core.capital_manager import CapitalManager
from core.logger import get_logger
from core.state_store import StateStore
from core.telegram_bot import TelegramBot
from core.time_utils import now_ist
from engine.anchor_engine import AnchorEngine
from engine.audit import DailyAudit
from engine.orchestrator import Orchestrator, _SymbolMeta
from engine.strategy_runner import StrategyConfig
from risk.guardrails import GuardRails

logger = get_logger("run_dhan")


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def _build_strategy_config() -> StrategyConfig:
    return StrategyConfig(
        trigger_dist=cfg.TRIGGER_DIST,
        tp_dist=cfg.TP_DIST,
        sl_dist=cfg.SL_DIST,
        lock_step=cfg.LOCK_STEP,
        lock_steps_count=cfg.LOCK_STEPS_COUNT,
        anchor_time=_parse_hhmm(cfg.ANCHOR_TIME),
        square_off_time=_parse_hhmm(cfg.SQUARE_OFF_TIME),
        tick_size=cfg.TICK_SIZE,
    )


def _resolve_mode(cli_mode: str) -> str:
    """LIVE requires both config.MODE=='LIVE' AND env MODE=='LIVE'.

    Anything else collapses to PAPER. Backtest is treated separately via
    a dedicated CLI path.
    """
    cli_upper = cli_mode.upper()
    if cli_upper == "BACKTEST":
        return "BACKTEST"
    if cli_upper == "LIVE":
        env_mode = (os.environ.get("MODE") or "").upper()
        config_mode = (cfg.MODE or "").upper()
        if env_mode != "LIVE" or config_mode != "LIVE":
            logger.error(
                "LIVE refused: requires MODE=LIVE in BOTH env and config.MODE "
                "(env=%r config=%r)", env_mode, config_mode,
            )
            return "REFUSED"
        return "LIVE"
    return "PAPER"


def _build_symbols(capital: float) -> list[_SymbolMeta]:
    eligible = CapitalManager(capital).allowed_symbols()
    return [
        _SymbolMeta(
            symbol=s["symbol"],
            security_id=str(s.get("security_id", "")),
            exchange_segment=s.get("segment", "NSE_FNO"),
            lot_size=int(s.get("lot_size", 1)),
        )
        for s in eligible
    ]


def _run_paper(args) -> int:
    strategy_cfg = _build_strategy_config()
    state_store = StateStore("orchestrator_state.json")
    anchor_engine = AnchorEngine(
        StateStore("anchors.json"),
        anchor_time=_parse_hhmm(cfg.ANCHOR_TIME),
        capture_window_seconds=cfg.ANCHOR_CAPTURE_WINDOW_SECONDS,
    )
    guardrails = GuardRails(
        max_daily_loss=cfg.MAX_DAILY_LOSS,
        max_trades_per_day=cfg.MAX_TRADES_PER_DAY,
    )
    audit = DailyAudit(date_iso=now_ist().date().isoformat())
    audit.set_mode("PAPER")

    broker = PaperBroker(starting_capital=cfg.MAX_DAILY_LOSS * 50)
    symbols = _build_symbols(broker.get_account().available_balance)
    if not symbols:
        logger.error("No eligible symbols at capital=%.2f", broker.get_account().available_balance)
        return 2

    orchestrator = Orchestrator(
        mode="PAPER",
        broker=broker,
        anchor_engine=anchor_engine,
        strategy_config=strategy_cfg,
        guardrails=guardrails,
        symbols=symbols,
        state_store=state_store,
        audit=audit,
        no_new_trade_after=_parse_hhmm(cfg.NO_NEW_TRADE_AFTER),
        telegram=TelegramBot() if args.heartbeat else None,
    )
    _install_signal_handlers(orchestrator)

    logger.info(
        "PAPER mode bootstrap complete. symbols=%s audit=%s",
        [s.symbol for s in symbols], audit.dir,
    )

    if args.dry_run:
        orchestrator.shutdown()
        return 0

    # In real PAPER mode the orchestrator is driven by the live Dhan
    # market feed (same WebSocket as LIVE). Until the operator wires
    # that up, we exit cleanly so the daily audit still emits. The
    # acceptance test drives `on_tick` directly without needing a feed.
    logger.info(
        "No feed driver wired for PAPER yet; flushing audit and exiting. "
        "Drive Orchestrator.on_tick(symbol, ltp) from your feed to run live."
    )
    orchestrator.shutdown()
    return 0


def _run_backtest(args) -> int:
    if not args.csv:
        logger.error("--csv required for backtest mode")
        return 2
    engine = BacktestEngine(_build_strategy_config())
    result = engine.run(
        csv_path=args.csv,
        quantity=args.quantity,
        money_per_point=args.money_per_point,
        m1_csv_path=args.m1_csv,
    )
    print("=" * 60)
    print(f"trades={result['trades']} wins={result['wins']} losses={result['losses']}")
    print(f"win_rate={result['win_rate']}% total_points={result['total_points_pnl']}")
    print(f"total_money={result['total_money_pnl']}")
    print("=" * 60)
    return 0


def _install_signal_handlers(orchestrator: Orchestrator) -> None:
    def _handler(signum, _frame):
        logger.info("Received signal %s; shutting down gracefully", signum)
        orchestrator.shutdown()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="run_dhan", description="Aureon Arvion Dhan trading agent")
    parser.add_argument("--mode", default="paper", choices=["paper", "live", "backtest"])
    parser.add_argument("--csv", help="OHLC CSV path (backtest)")
    parser.add_argument("--m1-csv", help="Optional M1 OHLC CSV for path-safe replay (backtest)")
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--money-per-point", type=float, default=1.0)
    parser.add_argument("--heartbeat", action="store_true", help="Enable Telegram heartbeat")
    parser.add_argument("--dry-run", action="store_true", help="Bootstrap + flush audit, then exit")
    args = parser.parse_args(argv)

    mode = _resolve_mode(args.mode)
    if mode == "REFUSED":
        return 3
    if mode == "BACKTEST":
        return _run_backtest(args)
    if mode == "LIVE":
        logger.error("LIVE orchestration loop is gated until the order-update "
                     "WebSocket subscription is wired; refusing for safety.")
        return 4
    return _run_paper(args)


if __name__ == "__main__":
    raise SystemExit(main())
