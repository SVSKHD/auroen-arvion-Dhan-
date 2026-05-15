# Audit: Pending Fixes for Aureon Arvion Dhan Agent

This document captures the current gap between the intended autonomous Dhan trading platform and the current repository foundation.

## Critical: Bot does not yet function as a real bot

### 1. Missing real Dhan broker adapter

`brokers/base.py` defines the broker contract, but there is no complete `DhanBroker` implementation yet.

Pending implementation:

- `get_ltp`
- `place_stop_order`
- `place_market_order`
- `cancel_order`
- `get_open_orders`
- `get_open_position`
- `modify_sl`
- `close_position`
- account balance / capital fetch

### 2. Missing orchestration layer

`run_dhan.py` currently only calculates and prints levels.

Required real flow:

```txt
fetch capital
→ resolve eligible symbols
→ resolve Dhan security IDs
→ connect Dhan websocket
→ capture 09:15 anchor
→ build thresholds
→ monitor ticks
→ trigger paper/live trade
→ manage TP/SL/lock
→ square off before close
→ update watchdog/API/Telegram
```

### 3. Backtest is too basic and currently optimistic

Current `backtest/backtest_engine.py` does not properly model:

- SL hits
- TP vs SL path order
- trailing locks
- square-off
- daily 09:15 anchor
- entry after anchor only
- brokerage/slippage
- drawdown
- realistic losing trades

Required fix:

```txt
one day = one simulation session
09:15 open/price = anchor
only bars after anchor are eligible
first threshold breach creates trade
then simulate TP/SL/lock/square-off
record realistic PnL
```

### 4. Dhan websocket requires production validation

Current `brokers/dhan/market_feed.py` is only an adapter-safe foundation.

Pending:

- verify Dhan v2 websocket URL
- verify auth format
- verify binary message parsing
- verify subscription payload
- parse LTP correctly
- reconnect on disconnect
- heartbeat / stale feed detection
- error logging instead of swallowing exceptions

## High-priority correctness issues

### 5. Anchor time must be config-driven and timezone-safe

Current anchor logic needs:

- use config value, not hardcoded time
- use India timezone
- capture once per day only
- persist anchor state
- reset daily
- recover anchor after restart

### 6. Paper mode and live mode must share the same engine

Paper mode must behave exactly like live mode except final order routing.

```txt
same websocket
same anchor
same threshold
same entry signal
same TP/SL/lock
same square-off
same logs/API/Telegram

PAPER → simulated execution
LIVE  → Dhan order execution
```

### 7. Trailing lock needs validation

Current lock behavior can place SL exactly at the current favorable boundary.

Need to decide strategy rule:

- first lock = breakeven, or
- first lock = small profit, or
- first lock = current logic

Then update both backtest and live/paper engines to match exactly.

### 8. Broker interface must support the actual strategy

The strategy needs more than market orders.

Required broker methods:

- place stop order
- place bracket / cover order if supported
- OCO / Forever Order support if used
- modify SL
- modify TP if required
- cancel pending order
- square off position
- get order book
- get trade book

## Medium-priority architecture gaps

### 9. README must match actual repo

README should be continuously updated as files are added.

Currently needed files:

- `brokers/dhan/dhan_broker.py`
- `brokers/dhan/historical_data.py`
- `engine/execution_engine.py`
- `engine/symbol_scorer.py`
- `engine/paper_runner.py`
- `engine/live_runner.py`
- `risk/guardrails.py`
- `core/state_store.py`
- `core/logger.py`
- `core/time_utils.py`

### 10. Risk layer is missing

Required:

- max daily loss
- max trades per day
- max open positions
- capital-based quantity sizing
- symbol-specific quantity rules
- market close square-off
- duplicate-trade protection
- order rejection handling
- API rate-limit protection

### 11. Single source of truth for config

Currently some values exist in multiple places.

Required:

- strategy distances only in config
- lock settings only in config
- symbols only in config or DB
- mode only in config/env with clear precedence
- no hardcoded constants in runners

### 12. Symbol security IDs are placeholders

Dhan needs valid instrument/security IDs.

Required:

- fetch/download instrument master
- resolve symbol names to security IDs
- cache resolved instruments
- validate segment/product type

### 13. Instrument-specific strategy parameters

One global trigger/TP/SL does not fit NIFTY, BANKNIFTY, stocks, and MCX GOLD.

Required:

```txt
symbol config:
  trigger_dist
  tp_dist
  sl_dist
  lock_step
  quantity rules
  min capital
  segment
  product type
```

### 14. Telegram receive loop is missing

Current Telegram layer can send and parse commands, but it does not receive updates.

Pending commands:

- `/status`
- `/restart`
- `/positions`
- `/paper`
- `/live`
- `/pnl`
- `/symbols`
- `/kill`

### 15. Dhan token expiry handling

Access-token lifecycle must be handled safely.

Required:

- startup validation
- expiry detection
- clear Telegram/API alert
- no silent trading failure

## Minor cleanup

- Add validation for positive distances.
- Make watchdog actually detect stale runtime/feed conditions.
- Replace direct file writes with atomic writes.
- Add structured logging.
- Add tests before trusting paper/backtest numbers.

## Prioritized fix order

1. Implement real `DhanBroker`.
2. Fix Dhan websocket message parsing and reconnect handling.
3. Build `engine/execution_engine.py` for paper/live parity.
4. Replace `run_dhan.py` with real orchestration loop.
5. Fix backtest engine to model SL, TP, trailing lock, square-off, and 09:15 anchor.
6. Add historical data fetcher.
7. Add symbol scorer.
8. Add risk guardrails.
9. Add Telegram polling daemon.
10. Expand FastAPI endpoints for Vue dashboard.
