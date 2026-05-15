# Aureon Arvion Dhan Agent

Broker-neutral Dhan migration of the Aureon Arvion anchor-breakout strategy. PAPER and LIVE share one strategy core; only the order sink differs.

## Running it

```bash
pip install -r requirements.txt
python -m pytest tests/                                # all tests
python run_dhan.py --mode=paper --dry-run              # bootstrap + flush audit, exit
python run_dhan.py --mode=backtest --csv=path/to/M5.csv [--m1-csv=path/to/M1.csv]
python run_dhan.py --mode=live                         # REFUSED unless both gates set
```

LIVE refusal: `python run_dhan.py --mode=live` exits 3 unless **both** the environment variable `MODE=LIVE` is set *and* `config.MODE == "LIVE"` in `config/strategy_config.py`. The check runs before the loop is constructed.

## Strategy

Anchor-breakout. At the configured anchor time (default 09:15 IST), capture the anchor price from the M5 bar open. Build long/short trigger levels off the anchor: `long_entry = anchor + trigger_dist`, `short_entry = anchor - trigger_dist`. Place an OCO stop pair at the broker; the first trigger crossed fills, the peer is cancelled. SL trails on the lagged close of each 5-min bar (Phase 2 fix — no within-bar look-ahead). Square-off at `square_off_time`. No new entries after `no_new_trade_after`.

`StrategyRunner` is the single source of truth for entry / TP / SL / trailing / square-off. BACKTEST and PAPER produce identical trade-by-trade results on the same OHLC stream (`tests/test_runner_parity.py`).

### R:R review

The module-level defaults are `TP_DIST=30` and `SL_DIST=40` — a **0.75:1 reward:risk**. This needs roughly a 57% win rate to break even *before* costs. The India intraday cost model (`costs/india_intraday.py`) makes the after-cost break-even higher — for an NIFTY-class round-trip near ₹19_800 entry it's about ₹609 of leakage per trade (see `tests/test_cost_model.py::test_nifty_futures_round_trip_components`).

Per-symbol defaults live in `SUPPORTED_SYMBOLS` (`config/strategy_config.py`):

```python
"strategy": {"trigger_dist": 20.0, "tp_dist": 30.0, "sl_dist": 40.0,
             "lock_step": 5.0, "lock_steps_count": 6},
```

The R:R defaults are intentionally **not** changed silently — tune per-symbol against backtest output before going live.

## Layout

```
api/server.py                       FastAPI: /health /status /heartbeat
                                    /anchors/today /positions /paper /audit/today
backtest/backtest_engine.py         M5/M1 backtest. Output includes
                                    total_gross_pnl, total_net_pnl, total_costs,
                                    max_drawdown_money/_pct, equity_curve,
                                    per-trade cost_breakdown.
brokers/
  base.py                           BrokerAdapter contract
  dhan/
    dhan_client.py                  Dhan v2 HTTP (retry, DhanAuthError on 401/403)
    dhan_broker.py                  BrokerAdapter over Dhan REST
    market_feed.py                  Binary v2 WebSocket feed (struct '<BHBIfI')
    historical_data.py              POST /charts/intraday + CSV cache + 429 backoff
    order_intents.py                Crash-safe order intent store
  paper/
    paper_broker.py                 BrokerAdapter stub for PAPER mode
config/
  strategy_config.py                Module-level defaults + per-symbol `strategy` block
core/
  capital_manager.py                Filters SUPPORTED_SYMBOLS by min_capital
  logger.py                         Structured JSON-per-line logger (LOG_FORMAT)
  state_store.py                    Atomic JSON writer (tmp + os.replace)
  telegram_bot.py                   Outbound /sendMessage wrapper
  telegram_polling.py               Long-poll daemon: /status /positions /pnl
                                    /pause /resume /kill, chat-id gated
  time_utils.py                     IST clock (now_ist)
costs/
  india_intraday.py                 IndiaIntradayCostModel — STT, exchange,
                                    SEBI, stamp duty, GST per round-trip
engine/
  anchor_engine.py                  IST-aware anchor capture w/ per-day persistence
  audit.py                          Daily triplet writer (events.jsonl,
                                    daily_summary.json, state_snapshot.json)
                                    with optional cost-model attachment
  execution_engine.py               Bar-driven engine; PAPER + LIVE shared
  live_runner.py                    Legacy live runner shim
  metrics.py                        Shared DrawdownTracker (audit + backtest)
  orchestrator.py                   Per-tick loop. PaperBroker fill simulation,
                                    OCO peer cancel, lagged-close trailing,
                                    pre-squareoff sweep, status_store writes.
  strategy_runner.py                Canonical anchor-breakout logic
  symbol_resolver.py                Capital → eligible symbols
  symbol_scorer.py                  Per-symbol backtest scoring
risk/
  guardrails.py                     Max daily loss + max trades / day
  position_sizer.py                 PositionSizer: capital × risk_pct sizing
strategy/
  levels.py                         build_order_levels (anchor + trigger/tp/sl)
tests/                              All passing (last verified 132 tests).
run_dhan.py                         CLI entry: --mode={paper,live,backtest}
.env.example                        Required env vars (DHAN_*, TELEGRAM_*)
```

## Audit files

Under `state/audit/<YYYY-MM-DD>/`:

- **`events.jsonl`** — append-only event log. Each line: `{"ts_ist":..., "type":..., "symbol":..., "payload":{...}}`. Survives a mid-session crash (each event is flushed individually). Event types: `anchor_captured`, `levels_computed`, `oco_pair_placed`, `order_filled`, `peer_cancelled`, `sl_modified`, `square_off`, `guardrail_blocked` (reasons: `MAX_TRADES_REACHED`, `MAX_DAILY_LOSS_HIT`, `position_size_zero`, `paused_via_telegram`), `oco_pair_cancelled_pre_squareoff`, `heartbeat`.
- **`daily_summary.json`** — end-of-day metrics: trade list with per-trade `costs` breakdown, `gross_pnl`, `net_pnl`, `total_costs`, win/loss, `max_drawdown_money` + `max_drawdown_pct`, `equity_curve`, anchors, mode, `cost_model_attached`.
- **`state_snapshot.json`** — final `StateStore` snapshot (orchestrator state).

Atomic writes throughout: a crash mid-flush leaves the previous valid file intact.

## API

`uvicorn api.server:app --host 0.0.0.0 --port 8000`. All endpoints are read-only and read from `StateStore` or the audit dir on disk (the orchestrator does not need to know the API exists).

| Endpoint | Returns |
|---|---|
| `GET /health` | `{"status":"ok"}` |
| `GET /status` | live orchestrator snapshot (mode, day_pnl, day_trades, positions, last_tick_at, healthy, paused) |
| `GET /heartbeat` | concise probe with `last_tick_age_seconds` and `feed_connected` |
| `GET /anchors/today` | today's anchor record per symbol |
| `GET /positions` | open positions + open OCO pairs |
| `GET /paper` | paper-mode closed trades |
| `GET /audit/today` | daily summary + `events_count` (line-counted, not loaded) |

## Telegram

Outbound heartbeat: every 5 minutes from the orchestrator (if `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set).

Inbound (`core.telegram_polling.TelegramPoller` long-polls `getUpdates`):

| Command | Effect |
|---|---|
| `/status` | mode + day_pnl + day_trades + paused + healthy |
| `/positions` | open position book |
| `/pnl` | cumulative day PnL (gross/net/drawdown if available) |
| `/pause` | sets `Orchestrator.paused = True` — new entries skipped; existing positions still managed |
| `/resume` | clears the pause flag |
| `/kill` | calls `on_kill` (orchestrator.shutdown) — destructive |

Security: only messages from the configured `TELEGRAM_CHAT_ID` are honored. Anything else is logged at WARNING level and dropped. `_last_update_id` advances for ignored messages too, so an attacker can't replay `/kill` into the next batch.

## Configuration

Module-level config in `config/strategy_config.py`:

- `MODE` — `"PAPER"` (default) or `"LIVE"`.
- `ANCHOR_TIME` (09:15), `SQUARE_OFF_TIME` (15:15), `NO_NEW_TRADE_AFTER` (14:30), `ANCHOR_CAPTURE_WINDOW_SECONDS` (30).
- `TRIGGER_DIST` / `TP_DIST` / `SL_DIST` / `LOCK_STEP` / `LOCK_STEPS_COUNT` — defaults used when a symbol omits the matching `strategy` key.
- `TICK_SIZE`, `MAX_RISK_PER_TRADE_PCT`, `MAX_DAILY_LOSS`, `MAX_TRADES_PER_DAY`.

Per-symbol overrides on each `SUPPORTED_SYMBOLS` entry. Each may carry a `strategy` block plus `money_per_point` and `instrument_class` (used by the cost model). `build_strategy_config(symbol_entry)` merges with module defaults.

Env vars (see `.env.example`):

- `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN` — Dhan v2 credentials (24-hour token lifetime).
- `MODE` — must be `LIVE` (and config.MODE must also be `"LIVE"`) to enable LIVE.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — outbound + inbound Telegram.
- `LOG_FORMAT` — `json` (default) or `plain` for local dev.
- `AUREON_LOG_FORMAT` — legacy alias of `LOG_FORMAT` for Phase 6a-era callers.

## Logs

JSON-per-line by default:

```jsonc
{"ts_ist":"2026-05-15T09:15:03+05:30","level":"INFO","logger":"orchestrator",
 "event":"anchor_captured","details":{"symbol":"NIFTY","price":19800.5}}

{"ts_ist":"2026-05-15T09:16:00+05:30","level":"INFO","logger":"execution_engine",
 "event":"legacy","message":"ENTRY | symbol=NIFTY side=LONG qty=50"}
```

`event: legacy` lines preserve the human-readable `message` so existing log-grep tooling keeps working. New call sites should use the structured form: `logger.info("event_name", extra={"key": value, ...})`. Set `LOG_FORMAT=plain` for human-readable output during local debugging.

## Tests

`python -m pytest tests/`. All passing (132 last verified). Notable files:

- `test_runner_parity.py` — BACKTEST ↔ PAPER trade-by-trade equivalence (Phase 1 invariant).
- `test_trailing_pathdep.py` — Phase 2 look-ahead fix; M5 lagged-close vs M1 intra-bar.
- `test_anchor_engine.py` — IST window boundaries, per-day persistence, restart restore.
- `test_dhan_broker_contract.py` — every `BrokerAdapter` method's verb/path/payload.
- `test_idempotent_orders.py` — pre-/post-HTTP crash scenarios.
- `test_market_feed_binary.py` — `<BHBIfI>` ticker decode against a hex fixture.
- `test_orchestrator.py` + `test_orchestrator_sizing.py` — tick loop, OCO peer cancel, trailing on bar boundary, sizer integration, `position_size_zero` audit event.
- `test_cost_model.py` — hand-derived NIFTY/BANKNIFTY/MCX totals against the rate constants.
- `test_drawdown.py` — shared `DrawdownTracker` semantics.
- `test_status_endpoint.py` — orchestrator status writes + `/status` migration.
- `test_telegram_polling.py` — command dispatch, chat-id security, offset advance.
- `test_historical_fetcher.py` — cache hit/miss, 429 backoff.
- `test_logger_json.py` — both call styles (legacy printf + structured event).
- `test_run_dhan_cli.py` — `--mode` gate (LIVE refusal).

## Phase history

- **Phase 1** — unified `StrategyRunner`, atomic state, paper/live parity foundation.
- **Phase 2** — trailing look-ahead fix (close-lagged trail; +20.0 pts / +66.67% edge inflation on parity fixture); M1 intra-bar replay.
- **Phase 3** — IST anchor engine with per-day persistence; `core.time_utils.now_ist`.
- **Phase 4** — real `DhanBroker` against Dhan v2 REST; binary market feed; idempotent order intents.
- **Phase 5** — orchestration loop; `PaperBroker`; daily audit triplet; `--mode` CLI gate.
- **Phase 6a (correctness)** — position sizing, per-symbol params, India intraday cost model, drawdown + equity curve.
- **Phase 6b (ops)** — `/status` migration from `core/watchdog.py`; `/heartbeat`, `/audit/today`; structured JSON logger; Telegram polling daemon; historical data fetcher; README rewrite.
