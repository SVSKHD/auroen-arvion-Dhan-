# Aureon Arvion Dhan Agent

Broker-neutral Dhan migration of the Aureon Arvion anchor-breakout strategy. PAPER and LIVE share one strategy core; only the order sink differs.

## Quickstart

```bash
pip install -r requirements.txt
python -m pytest tests/                 # 89 passing
python run_dhan.py --mode=paper --dry-run
python run_dhan.py --mode=backtest --csv=path/to/M5.csv [--m1-csv=path/to/M1.csv]
```

LIVE is **refused** unless both `MODE=LIVE` is set in the environment *and* `config.MODE == "LIVE"` in `config/strategy_config.py`. Misalignment exits 3 before the loop is constructed.

## Strategy

Anchor-breakout. At the configured anchor time (default 09:15 IST), capture the anchor price from the M5 bar open. Build long/short trigger levels off the anchor: `long_entry = anchor + trigger_dist`, `short_entry = anchor - trigger_dist`. Place an OCO stop pair at the broker; first trigger crossed fills, peer is cancelled. SL trails on the lagged close of each 5-min bar (Phase 2 fix — no within-bar look-ahead). Square-off at `square_off_time`.

`StrategyRunner` is the single source of truth for entry / TP / SL / trailing / square-off. BACKTEST and PAPER produce identical trade-by-trade results on the same OHLC stream (pinned by `tests/test_runner_parity.py`).

## Layout

```
api/server.py                       FastAPI surface (reads StateStore + audit dir)
backtest/backtest_engine.py         M5/M1 backtest with cost model + drawdown + equity curve
brokers/
  base.py                           BrokerAdapter contract
  dhan/
    dhan_client.py                  Dhan v2 HTTP client (retry, DhanAuthError)
    dhan_broker.py                  BrokerAdapter over Dhan REST
    market_feed.py                  Binary v2 WebSocket feed (struct '<BHBIfI')
    historical_data.py              POST /charts/intraday + CSV cache
    order_intents.py                Crash-safe order intent store
  paper/
    paper_broker.py                 In-memory BrokerAdapter for PAPER mode
config/
  strategy_config.py                Defaults + per-symbol overrides
core/
  logger.py                         Structured JSON-per-line logger
  state_store.py                    Atomic JSON writer (tmp + os.replace)
  time_utils.py                     IST clock (`now_ist`)
  capital_manager.py                Filters SUPPORTED_SYMBOLS by min_capital
  telegram_bot.py                   send + long-poll command daemon
  watchdog.py                       Runtime status writer (atomic)
costs/
  india_intraday.py                 STT, exchange, SEBI, GST, stamp duty per round-trip
engine/
  anchor_engine.py                  IST-aware anchor capture, per-day persistence
  audit.py                          Daily triplet writer (events.jsonl, summary, snapshot)
  execution_engine.py               Bar-driven engine; PAPER + LIVE share this
  live_runner.py                    Legacy live runner shim
  orchestrator.py                   Per-tick orchestration loop (Phase 5)
  strategy_runner.py                Canonical anchor-breakout logic
  symbol_resolver.py                Capital -> eligible symbols
  symbol_scorer.py                  Per-symbol backtest scoring
risk/
  guardrails.py                     Max daily loss + max trades/day
  position_sizer.py                 Capital * risk_pct / (sl_dist * money_per_point)
strategy/
  levels.py                         build_order_levels (anchor + trigger/tp/sl)
tests/                              89 tests across all of the above
run_dhan.py                         CLI entry (--mode={paper,live,backtest})
```

## Audit trail

The orchestrator writes a daily triplet under `state/audit/<YYYY-MM-DD>/`:

- `events.jsonl` — append-only event log. Survives a mid-session crash because each event is flushed individually. Event types: `anchor_captured`, `levels_computed`, `oco_pair_placed`, `order_filled`, `peer_cancelled`, `sl_modified`, `square_off`, `guardrail_blocked`, `heartbeat`.
- `daily_summary.json` — end-of-day metrics: trade list, gross/net PnL, win/loss, max drawdown, anchors, mode.
- `state_snapshot.json` — final StateStore snapshot.

## API

Run `uvicorn api.server:app --host 0.0.0.0 --port 8000`. Endpoints (all read-only):

- `GET /health` — liveness
- `GET /heartbeat` — mode + day_pnl + day_trades + positions
- `GET /status` — raw runtime status from `core.watchdog`
- `GET /anchors/today` — today's anchors per symbol
- `GET /positions` — open positions + open OCO pairs + day PnL
- `GET /paper` — paper-mode closed trades
- `GET /audit/today` — daily summary + last 200 events

## Telegram

If `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set, the orchestrator sends a heartbeat every 5 minutes. The polling daemon (`core.telegram_bot.TelegramPoller`) accepts `/status`, `/positions`, `/pnl`, `/pause` (skip new entries), `/resume`, `/kill` (graceful shutdown).

## Configuration

Per-symbol overrides live in `SUPPORTED_SYMBOLS` (`config/strategy_config.py`). Each entry can override `trigger_dist`, `tp_dist`, `sl_dist`, `lock_step`, `money_per_point`, `instrument_class`. Anything absent falls back to the module-level default.

### R:R note

The module-level default is `TP_DIST=30` vs `SL_DIST=40` — a 0.75:1 reward:risk. This requires roughly a 57% win rate to break even *before* costs. The cost model (`costs/india_intraday.py`) makes the after-cost requirement materially higher. Operators should tune per-symbol distances based on backtest output before going live.

## Backtest output

`BacktestEngine.run(...)` returns:

```python
{
    "trades": int,
    "wins": int, "losses": int, "win_rate": float,
    "total_points_pnl": float,
    "gross_pnl": float,
    "net_pnl": float,            # gross minus costs
    "total_costs": float,
    "max_drawdown_money": float,
    "max_drawdown_pct": float,
    "equity_curve": [(iso_ts, cumulative_net), ...],
    "trade_results": [
        {... "cost_breakdown": {...}, "net_money_pnl": float},
        ...
    ],
}
```

Pass `instrument_class="EQUITY_INDEX_FUT"` (or `"EQUITY_INDEX_OPT"`, `"MCX_COMM_FUT"`) to apply the cost model.

## Phase history

- **Phase 1** — unified `StrategyRunner`, atomic state, paper/live parity foundation.
- **Phase 2** — trailing look-ahead fix (close-lagged trail; +20.0 points / +66.67% edge inflation on the parity fixture). M1 intra-bar replay.
- **Phase 3** — IST anchor engine with per-day persistence; `core.time_utils.now_ist`.
- **Phase 4** — real `DhanBroker` against Dhan v2 REST; binary market feed; idempotent order intents.
- **Phase 5** — orchestration loop; PaperBroker; daily audit triplet; `--mode` CLI gate.
- **Phase 6** — position sizing, per-symbol params, India intraday cost model, drawdown + equity curve, API endpoints, Telegram polling daemon, historical data fetcher, JSON logs.
