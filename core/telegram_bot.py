"""Outbound Telegram bot — send-only.

`TelegramBot.send(text)` posts to /sendMessage. Silently no-ops when
`TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` is unset so dev environments
don't fail.

Inbound long-polling lives in `core/telegram_polling.py` so it can be
imported optionally without pulling in threading at module import time.
"""

import os
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


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
        except requests.RequestException:
            # Outbound errors are logged by the poller's reply path; the
            # send path itself stays silent so we don't recurse if an
            # error handler tries to send.
            pass

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
