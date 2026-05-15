"""Thin HTTP client for the Dhan v2 REST API.

Verified against the official DhanHQ-py SDK (dhan-oss/DhanHQ-py, file
dhan_http.py): the live API uses the `access-token` and `client-id` headers
against `https://api.dhan.co/v2` and embeds `dhanClientId` in JSON request
bodies. Headers and payload field names are *not guessed*; they match the
SDK shipped by Dhan.

Retry policy:
  - 5xx and 429: exponential backoff (0.5, 1, 2, 4s), max 4 attempts.
  - 401 / 403: raise DhanAuthError immediately — the orchestrator must alert
    and refuse to trade, not silently retry against an expired token.
  - other 4xx: raise DhanHttpError; usually a payload error and not
    retryable.
"""

import os
import time
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.dhan.co/v2"
DEFAULT_TIMEOUT = 10.0
RETRY_BACKOFFS = (0.5, 1.0, 2.0, 4.0)
RETRY_STATUS = {429, 500, 502, 503, 504}


class DhanError(Exception):
    pass


class DhanAuthError(DhanError):
    """Raised on 401/403. The access token is invalid or expired; tokens
    last 24h, so this typically means the daily token regeneration step
    has not run.
    """


class DhanHttpError(DhanError):
    def __init__(self, status_code: int, body: Any) -> None:
        super().__init__(f"HTTP {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class DhanClient:
    def __init__(
        self,
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
        base_url: str = BASE_URL,
        session: Optional[requests.Session] = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.client_id = client_id or os.environ.get("DHAN_CLIENT_ID", "")
        self.access_token = access_token or os.environ.get("DHAN_ACCESS_TOKEN", "")
        self.base_url = base_url
        self._session = session or requests.Session()
        self._sleep = sleep

    def _headers(self) -> dict[str, str]:
        return {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-type": "application/json",
            "Accept": "application/json",
        }

    def get(self, path: str) -> Any:
        return self._request("GET", path, None)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, payload)

    def put(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("PUT", path, payload)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path, None)

    def _request(self, method: str, path: str, payload: Optional[dict[str, Any]]) -> Any:
        url = f"{self.base_url}{path}"
        body: Optional[dict[str, Any]] = None
        if payload is not None:
            body = dict(payload)
            body["dhanClientId"] = self.client_id

        last_status: Optional[int] = None
        last_body: Any = None

        for attempt in range(len(RETRY_BACKOFFS) + 1):
            response = self._session.request(
                method=method,
                url=url,
                headers=self._headers(),
                json=body,
                timeout=DEFAULT_TIMEOUT,
            )

            if response.status_code in (401, 403):
                raise DhanAuthError(
                    f"{response.status_code} from Dhan; access token may have "
                    f"expired (24h lifetime) or static IP not whitelisted."
                )

            if response.status_code in RETRY_STATUS:
                last_status = response.status_code
                last_body = self._safe_body(response)
                if attempt < len(RETRY_BACKOFFS):
                    self._sleep(RETRY_BACKOFFS[attempt])
                    continue
                raise DhanHttpError(last_status, last_body)

            if not (200 <= response.status_code < 300):
                raise DhanHttpError(response.status_code, self._safe_body(response))

            return self._safe_body(response)

        # Defensive — loop body always returns or raises.
        raise DhanHttpError(last_status or 0, last_body)

    @staticmethod
    def _safe_body(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return response.text
