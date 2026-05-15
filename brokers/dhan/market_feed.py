import json
import os
import threading
from typing import Any

from dotenv import load_dotenv
from websocket import WebSocketApp

load_dotenv()


class DhanMarketFeed:
    """
    Real-time Dhan market feed layer.

    NOTE:
    WebSocket payload structure may evolve from Dhan.
    This adapter isolates feed logic so the rest of the bot remains stable.
    """

    def __init__(self) -> None:
        self.access_token = os.environ.get("DHAN_ACCESS_TOKEN", "")
        self.client_id = os.environ.get("DHAN_CLIENT_ID", "")

        self.ws_url = (
            "wss://api-feed.dhan.co"
        )

        self.latest_ticks: dict[str, dict[str, Any]] = {}
        self.ws: WebSocketApp | None = None

    def connect(self, instruments: list[dict]) -> None:
        def on_open(ws):
            payload = {
                "RequestCode": 15,
                "InstrumentCount": len(instruments),
                "InstrumentList": instruments,
            }

            ws.send(json.dumps(payload))

        def on_message(ws, message):
            try:
                data = json.loads(message)

                security_id = str(data.get("security_id", "UNKNOWN"))
                self.latest_ticks[security_id] = data

            except Exception:
                pass

        def on_error(ws, error):
            print("Market feed error:", error)

        def on_close(ws, close_status_code, close_msg):
            print("Market feed closed")

        self.ws = WebSocketApp(
            self.ws_url,
            header={
                "access-token": self.access_token,
                "client-id": self.client_id,
            },
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        thread = threading.Thread(target=self.ws.run_forever, daemon=True)
        thread.start()

    def get_ltp(self, security_id: str) -> float | None:
        tick = self.latest_ticks.get(str(security_id))

        if not tick:
            return None

        return tick.get("LTP") or tick.get("ltp")
