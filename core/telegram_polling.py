"""Telegram long-polling daemon.

Runs on a background thread, pulls `getUpdates` with the standard
`offset` cursor, dispatches commands to handlers, and replies via the
existing `TelegramBot`.

Security:
    Only messages from the configured `TELEGRAM_CHAT_ID` are honored.
    Any other sender is logged as a WARNING event and dropped. This
    matters — `/kill` is destructive (shuts the bot down). Without the
    chat-id check, anyone who knew the bot token could drive it.

Threading:
    The poll thread is a daemon. `stop()` flips an `Event` and the next
    iteration unwinds. Network errors are logged and the loop continues.

Offset handling:
    Telegram's getUpdates returns each update once unless you ack it via
    `offset = last_update_id + 1`. Without offset advancement, restart
    replays the last batch of commands, which would re-execute `/kill`.
"""

import os
import threading
from typing import Any, Callable, Optional

import requests
from dotenv import load_dotenv

from core.logger import get_logger
from core.telegram_bot import TelegramBot

load_dotenv()

logger = get_logger("telegram_polling")


class TelegramPoller:
    """Background long-poll daemon over Telegram's Bot API.

    Construction is cheap; `start()` spawns the thread. Without a bot
    token, the poller is a no-op so dev environments don't fail or
    spawn idle threads.
    """

    def __init__(
        self,
        bot: Optional[TelegramBot] = None,
        authorized_chat_id: Optional[str] = None,
        on_kill: Optional[Callable[[], None]] = None,
        status_provider: Optional[Callable[[], dict]] = None,
        positions_provider: Optional[Callable[[], dict]] = None,
        pnl_provider: Optional[Callable[[], dict]] = None,
        pause_setter: Optional[Callable[[bool], None]] = None,
        poll_interval_seconds: float = 2.0,
        long_poll_timeout: int = 15,
        http_session: Optional[requests.Session] = None,
    ) -> None:
        self.bot = bot or TelegramBot()
        self.authorized_chat_id = str(
            authorized_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        )
        self.on_kill = on_kill
        self.status_provider = status_provider or (lambda: {})
        self.positions_provider = positions_provider or (lambda: {})
        self.pnl_provider = pnl_provider or (lambda: {})
        self.pause_setter = pause_setter
        self.poll_interval_seconds = poll_interval_seconds
        self.long_poll_timeout = long_poll_timeout
        self.http = http_session or requests.Session()

        self.state = {"paused": False, "kill_requested": False}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_update_id = 0

    # --------------------------------------------------------- lifecycle

    def start(self) -> None:
        if not self.bot.bot_token:
            logger.info("telegram_poller_disabled", extra={"reason": "no_bot_token"})
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # --------------------------------------------------------- dispatch

    def handle_command(self, command: str) -> str:
        if command == "STATUS":
            return self._format_status()
        if command == "POSITIONS":
            return self._format_positions()
        if command == "PNL":
            return self._format_pnl()
        if command == "PAUSE":
            self.state["paused"] = True
            if self.pause_setter is not None:
                self.pause_setter(True)
            return "Paused. /resume to re-enable entries."
        if command == "RESUME":
            self.state["paused"] = False
            if self.pause_setter is not None:
                self.pause_setter(False)
            return "Resumed."
        if command == "KILL":
            self.state["kill_requested"] = True
            if self.on_kill is not None:
                try:
                    self.on_kill()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("on_kill_handler_raised", extra={"err": str(exc)})
            return "Shutdown requested."
        return (
            "Unknown command. Try /status /positions /pnl /pause /resume /kill"
        )

    # --------------------------------------------------------- poll loop

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:  # noqa: BLE001 — never die in the loop
                logger.warning(
                    "telegram_poll_iteration_failed",
                    extra={"err": str(exc), "type": type(exc).__name__},
                )
            self._stop.wait(self.poll_interval_seconds)

    def _poll_once(self) -> None:
        url = f"https://api.telegram.org/bot{self.bot.bot_token}/getUpdates"
        params = {
            "timeout": self.long_poll_timeout,
            "offset": self._last_update_id + 1,
        }
        try:
            response = self.http.get(url, params=params, timeout=self.long_poll_timeout + 5)
        except requests.RequestException as exc:
            logger.warning("getUpdates_failed", extra={"err": str(exc)})
            return
        if response.status_code != 200:
            logger.warning("getUpdates_http_error", extra={"status": response.status_code})
            return
        try:
            data = response.json()
        except ValueError:
            return

        for update in data.get("result") or []:
            self._handle_update(update)

    def _handle_update(self, update: dict[str, Any]) -> None:
        # Always advance the offset, even if we ignore the message — the
        # alternative replays the same update next call.
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            self._last_update_id = max(self._last_update_id, update_id)

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (message.get("text") or "").strip()
        if not text:
            return

        if self.authorized_chat_id and chat_id != self.authorized_chat_id:
            logger.warning(
                "telegram_unauthorized_sender",
                extra={
                    "chat_id": chat_id,
                    "from": (message.get("from") or {}).get("username"),
                    "text": text[:80],
                },
            )
            return

        command = TelegramBot.parse_command(text)
        reply = self.handle_command(command)
        try:
            self.bot.send(reply)
        except Exception as exc:  # noqa: BLE001 — never die on reply failure
            logger.warning("telegram_send_failed", extra={"err": str(exc)})

    # --------------------------------------------------------- formatters

    def _format_status(self) -> str:
        s = self.status_provider() or {}
        return (
            f"mode={s.get('mode', '?')}  "
            f"day_pnl={s.get('day_pnl', 0):.2f}  "
            f"trades={s.get('day_trades', 0)}  "
            f"paused={self.state['paused']}  "
            f"healthy={s.get('healthy', False)}"
        )

    def _format_positions(self) -> str:
        s = self.positions_provider() or {}
        positions = s.get("positions") or {}
        if not positions:
            return "No open positions."
        lines = []
        for sym, p in positions.items():
            if isinstance(p, dict):
                lines.append(
                    f"{sym} {p.get('side')} @ {p.get('entry_price')} "
                    f"qty={p.get('quantity')}  SL={p.get('sl')}  TP={p.get('tp')}"
                )
            else:
                lines.append(f"{sym}: {p}")
        return "\n".join(lines)

    def _format_pnl(self) -> str:
        s = self.pnl_provider() or {}
        if "trade_count" in s:
            return (
                f"Today: trades={s.get('trade_count', 0)}  "
                f"gross={s.get('gross_pnl', 0):.2f}  "
                f"net={s.get('net_pnl', 0):.2f}  "
                f"drawdown={s.get('max_drawdown_money', 0):.2f}"
            )
        return f"day_pnl={s.get('day_pnl', 0):.2f}  trades={s.get('day_trades', 0)}"
