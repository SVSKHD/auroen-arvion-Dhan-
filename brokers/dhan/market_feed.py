"""Dhan v2 market feed over WebSocket.

The v2 protocol differs from v1 in three important ways, all of which the
pre-Phase-4 implementation got wrong:

  - Auth is via URL query string, NOT headers:
        wss://api-feed.dhan.co?version=2&token=<ACCESS_TOKEN>
            &clientId=<CLIENT_ID>&authType=2
  - Server-to-client packets are BINARY (not JSON). Subscribe messages
    sent client-to-server are JSON.
  - Dhan disconnects after ~40s of silence. The client must respond to or
    initiate pings.

Ticker packet layout (verified against dhan-oss/DhanHQ-py marketfeed.py):

    struct '<BHBIfI', 16 bytes total:
        B  - response code (2 = ticker, 50 = server disconnect)
        H  - message length
        B  - exchange segment id
        I  - security id
        f  - LTP (float32)
        I  - LTT (epoch seconds)

This module exposes a `parse_ticker(bytes) -> Tick | None` helper that the
test suite drives with a hex fixture; the live WebSocket handler reuses it.

Reconnect: exponential backoff 1, 2, 4, 8, capped at 30s. The `latest_ticks`
dict is guarded by a lock because tick callbacks fire on the websocket-client
thread while strategy code reads on the main loop thread.
"""

import json
import os
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from dotenv import load_dotenv

from core.logger import get_logger

load_dotenv()

logger = get_logger("dhan_market_feed")

WSS_BASE = "wss://api-feed.dhan.co"
TICKER_FMT = "<BHBIfI"
TICKER_LEN = struct.calcsize(TICKER_FMT)
TICKER_RESPONSE_CODE = 2
SERVER_DISCONNECT_CODE = 50
RECONNECT_DELAYS = (1, 2, 4, 8, 16, 30)
PING_INTERVAL_SECONDS = 25  # under Dhan's ~40s idle disconnect threshold


@dataclass
class Tick:
    response_code: int
    exchange_segment: int
    security_id: int
    ltp: float
    ltt_epoch: int


def parse_ticker(payload: bytes) -> Optional[Tick]:
    """Parse the 16-byte Dhan v2 ticker packet. Returns None for non-ticker
    response codes (the caller dispatches Quote / Full / Disconnect).
    """
    if len(payload) < TICKER_LEN:
        return None
    response_code, _msg_len, segment, security_id, ltp, ltt = struct.unpack(
        TICKER_FMT, payload[:TICKER_LEN]
    )
    if response_code != TICKER_RESPONSE_CODE:
        return None
    return Tick(
        response_code=response_code,
        exchange_segment=segment,
        security_id=security_id,
        ltp=float(ltp),
        ltt_epoch=int(ltt),
    )


def is_server_disconnect(payload: bytes) -> bool:
    if not payload:
        return False
    return payload[0] == SERVER_DISCONNECT_CODE


class DhanMarketFeed:
    def __init__(
        self,
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
        on_tick: Optional[Callable[[Tick], None]] = None,
    ) -> None:
        self.client_id = client_id or os.environ.get("DHAN_CLIENT_ID", "")
        self.access_token = access_token or os.environ.get("DHAN_ACCESS_TOKEN", "")
        self.on_tick = on_tick

        self._ticks_lock = threading.Lock()
        self.latest_ticks: dict[str, Tick] = {}

        self.ws: Any = None
        self._ws_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._instruments: list[dict] = []
        self._reconnect_idx = 0

    # ------------------------------------------------------------------ public

    def latest_ltp(self, security_id: str) -> Optional[float]:
        with self._ticks_lock:
            tick = self.latest_ticks.get(str(security_id))
            return tick.ltp if tick else None

    def snapshot(self) -> dict[str, Tick]:
        with self._ticks_lock:
            return dict(self.latest_ticks)

    def connect(self, instruments: list[dict]) -> None:
        self._instruments = instruments
        self._stop.clear()
        self._ws_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._ws_thread.start()

    def close(self) -> None:
        self._stop.set()
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception as exc:  # noqa: BLE001 — close best effort
                logger.warning("Error closing websocket: %s", exc)

    # --------------------------------------------------------------- internal

    def _ws_url(self) -> str:
        return (
            f"{WSS_BASE}?version=2&token={self.access_token}"
            f"&clientId={self.client_id}&authType=2"
        )

    def _run_loop(self) -> None:
        from websocket import WebSocketApp

        while not self._stop.is_set():
            try:
                self.ws = WebSocketApp(
                    self._ws_url(),
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_ping=self._on_ping,
                    on_pong=self._on_pong,
                )
                self.ws.run_forever(
                    ping_interval=PING_INTERVAL_SECONDS,
                    ping_timeout=10,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("WebSocket run_forever raised: %s", exc)

            if self._stop.is_set():
                return
            delay = RECONNECT_DELAYS[min(self._reconnect_idx, len(RECONNECT_DELAYS) - 1)]
            self._reconnect_idx += 1
            logger.warning("Market feed reconnecting in %ds (attempt #%d)", delay, self._reconnect_idx)
            time.sleep(delay)

    def _on_open(self, ws) -> None:
        self._reconnect_idx = 0
        logger.info("Market feed connected; subscribing %d instruments", len(self._instruments))
        subscription_message = {
            "RequestCode": 15,
            "InstrumentCount": len(self._instruments),
            "InstrumentList": self._instruments,
        }
        ws.send(json.dumps(subscription_message))

    def _on_message(self, _ws, message) -> None:
        if isinstance(message, str):
            logger.debug("Text message from feed: %s", message)
            return

        if is_server_disconnect(message):
            logger.warning("Server-initiated disconnect packet; will reconnect")
            try:
                self.ws.close()
            except Exception:
                pass
            return

        tick = parse_ticker(message)
        if tick is None:
            # Log enough hex to debug, but cap so we don't flood logs.
            hex_head = message[:32].hex() if isinstance(message, (bytes, bytearray)) else "n/a"
            logger.warning("Unknown packet (head=%s, len=%d)", hex_head, len(message))
            return

        with self._ticks_lock:
            self.latest_ticks[str(tick.security_id)] = tick

        if self.on_tick is not None:
            try:
                self.on_tick(tick)
            except Exception as exc:  # noqa: BLE001 — callback owns its errors
                logger.exception("on_tick callback raised: %s", exc)

    def _on_error(self, _ws, error) -> None:
        logger.error("Market feed error: %s", error)

    def _on_close(self, _ws, status_code, msg) -> None:
        logger.warning("Market feed closed: status=%s msg=%s", status_code, msg)

    def _on_ping(self, _ws, data) -> None:
        logger.debug("PING received from server")

    def _on_pong(self, _ws, data) -> None:
        logger.debug("PONG received from server")
