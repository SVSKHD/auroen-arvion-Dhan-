"""Telegram bot — send + long-poll command daemon.

Outbound: `TelegramBot.send(text)` posts to /sendMessage. Silently no-ops
if BOT_TOKEN or CHAT_ID is unset so dev environments don't fail.

Inbound: `TelegramPoller` runs `getUpdates` on a background thread and
dispatches commands. Supported commands and their semantics:

  /status     - reply with mode + day_pnl + open positions
  /positions  - reply with the position book
  /pnl        - reply with cumulative day PnL
  /pause      - flip a flag; orchestrator skips new entries until /resume
  /resume     - clear the pause flag
  /kill       - call `on_kill` (the orchestrator's graceful shutdown)

The poller is intentionally simple — no async, no third-party telegram
SDK. We use the public Bot API directly with `requests`.
"""

import os
import threading
import time as time_module
from typing import Callable, Optional

import requests
from dotenv import load_dotenv

from core.logger import get_logger

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

logger = get_logger("telegram_bot")


class TelegramBot:
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None) -> None:
        self.bot_token = bot_token or BOT_TOKEN
        self.chat_id = chat_id or CHAT_ID

    def send(self, text: str) -> None:
        if not self.bot_token or not self.chat_id:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.warning("Telegram send failed: %s", exc)

    @staticmethod
    def parse_command(text: str) -> str:
        text = text.strip().lower()
        if text == "/status":
            return "STATUS"
        if text == "/positions":
            return "POSITIONS"
        if text == "/pnl":
            return "PNL"
        if text == "/pause":
            return "PAUSE"
        if text == "/resume":
            return "RESUME"
        if text == "/kill":
            return "KILL"
        if text == "/restart":
            return "RESTART"
        return "UNKNOWN"


class TelegramPoller:
    """Long-polls Telegram for commands. Updates a `state` dict the
    orchestrator can read (`paused`, `kill_requested`). Calls `on_kill`
    when the user types /kill.
    """

    def __init__(
        self,
        bot: Optional[TelegramBot] = None,
        on_kill: Optional[Callable[[], None]] = None,
        status_provider: Optional[Callable[[], dict]] = None,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self.bot = bot or TelegramBot()
        self.on_kill = on_kill
        self.status_provider = status_provider or (lambda: {})
        self.poll_interval_seconds = poll_interval_seconds

        self.state = {"paused": False, "kill_requested": False}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_update_id = 0

    def start(self) -> None:
        if not self.bot.bot_token:
            logger.info("Telegram poller idle: no BOT_TOKEN")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def handle_command(self, command: str) -> str:
        if command == "STATUS":
            return self._format_status()
        if command == "POSITIONS":
            return self._format_positions()
        if command == "PNL":
            return self._format_pnl()
        if command == "PAUSE":
            self.state["paused"] = True
            return "Paused. /resume to re-enable entries."
        if command == "RESUME":
            self.state["paused"] = False
            return "Resumed."
        if command == "KILL":
            self.state["kill_requested"] = True
            if self.on_kill is not None:
                try:
                    self.on_kill()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("on_kill handler raised: %s", exc)
            return "Shutdown requested."
        return "Unknown command. Try /status /positions /pnl /pause /resume /kill"

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Telegram poll iteration failed: %s", exc)
            self._stop.wait(self.poll_interval_seconds)

    def _poll_once(self) -> None:
        url = f"https://api.telegram.org/bot{self.bot.bot_token}/getUpdates"
        params = {"timeout": 15, "offset": self._last_update_id + 1}
        try:
            response = requests.get(url, params=params, timeout=20)
        except requests.RequestException as exc:
            logger.warning("getUpdates failed: %s", exc)
            return
        if response.status_code != 200:
            return
        try:
            data = response.json()
        except ValueError:
            return
        for update in (data.get("result") or []):
            self._last_update_id = update.get("update_id", self._last_update_id)
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            if not text:
                continue
            command = TelegramBot.parse_command(text)
            reply = self.handle_command(command)
            self.bot.send(reply)

    def _format_status(self) -> str:
        s = self.status_provider() or {}
        return (
            f"mode={s.get('mode', '?')}  "
            f"day_pnl={s.get('day_pnl', 0):.2f}  "
            f"trades={s.get('day_trades', 0)}  "
            f"paused={self.state['paused']}"
        )

    def _format_positions(self) -> str:
        s = self.status_provider() or {}
        positions = s.get("positions") or {}
        if not positions:
            return "No open positions."
        lines = []
        for sym, p in positions.items():
            if isinstance(p, dict):
                lines.append(f"{sym} {p.get('side')} @ {p.get('entry_price')}  SL={p.get('sl')}")
            else:
                lines.append(f"{sym}: {p}")
        return "\n".join(lines)

    def _format_pnl(self) -> str:
        s = self.status_provider() or {}
        return f"day_pnl={s.get('day_pnl', 0):.2f}  trades={s.get('day_trades', 0)}"
