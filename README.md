# Aureon Arvion Dhan Agent

Broker-neutral Dhan migration of the Aureon Arvion anchor-breakout strategy.

## Goal

Keep the strategy core from the Forex/MT5 bot, but run it through a Dhan adapter for Indian markets.

## Current V1 Scope

- Anchor price capture
- Upper/lower trigger calculation
- OCO-style entry flow
- TP / SL price levels
- Progressive lock/trailing calculation
- Dhan REST client skeleton
- Dry-run mode by default
- Intraday square-off safety

## Not enabled in V1

- Rescue hedge
- Dual bracket
- Weekly profit lock
- Live WebSocket feed
- Production auto-trading without validation

These will be added only after the basic Dhan flow is proven.

## Project Structure

```txt
config/
  dhan_config.py
  strategy_config.py
core/
  logger.py
  state_store.py
  time_utils.py
strategy/
  levels.py
  trailing.py
  anchor_strategy.py
brokers/
  base.py
  dhan/
    dhan_client.py
    dhan_broker.py
risk/
  guardrails.py
run_dhan.py
```
