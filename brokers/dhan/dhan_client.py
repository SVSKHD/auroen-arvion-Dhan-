import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


class DhanClient:
    def __init__(self) -> None:
        self.client_id = os.environ.get("DHAN_CLIENT_ID", "")
        self.access_token = os.environ.get("DHAN_ACCESS_TOKEN", "")
        self.base_url = "https://api.dhan.co/v2"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "access-token": self.access_token,
            "client-id": self.client_id,
        }

    def get(self, path: str) -> dict[str, Any]:
        response = requests.get(
            f"{self.base_url}{path}",
            headers=self._headers(),
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            f"{self.base_url}{path}",
            headers=self._headers(),
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
