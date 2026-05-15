import os

import requests
from dotenv import load_dotenv

load_dotenv()


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


class TelegramBot:
    def send(self, text: str) -> None:
        if not BOT_TOKEN or not CHAT_ID:
            return

        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
            },
            timeout=10,
        )

    def parse_command(self, text: str) -> str:
        text = text.strip().lower()

        if text == "/status":
            return "STATUS"

        if text == "/restart":
            return "RESTART"

        return "UNKNOWN"
