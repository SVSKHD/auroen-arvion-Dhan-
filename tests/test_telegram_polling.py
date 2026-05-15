"""Telegram polling daemon — command dispatch, security, offset.

We mock `getUpdates` HTTP entirely so no Telegram calls leave the box.
The poller's threading is exercised only briefly: most tests call
`handle_update` directly to avoid sleeps.
"""

import json
from typing import Any

import pytest

from core.telegram_bot import TelegramBot
from core.telegram_polling import TelegramPoller


class _MockSession:
    """Returns scripted responses for sequential `getUpdates` calls."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        if not self.responses:
            return _Resp(200, {"ok": True, "result": []})
        entry = self.responses.pop(0)
        if isinstance(entry, Exception):
            raise entry
        return _Resp(*entry)


class _Resp:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if not isinstance(body, str) else body

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


def _poller(**kw) -> TelegramPoller:
    return TelegramPoller(
        bot=TelegramBot(bot_token="testtoken", chat_id="555"),
        authorized_chat_id="555",
        **kw,
    )


# --------------------------------------------------------- command parsing


def test_parse_command_covers_pass_b_set():
    p = TelegramBot.parse_command
    assert p("/status") == "STATUS"
    assert p("/positions") == "POSITIONS"
    assert p("/pnl") == "PNL"
    assert p("/pause") == "PAUSE"
    assert p("/resume") == "RESUME"
    assert p("/kill") == "KILL"
    assert p("/garbage") == "UNKNOWN"


# --------------------------------------------------------- dispatch


def test_pause_resume_flips_state_and_calls_setter():
    pause_calls: list[bool] = []
    poller = _poller(pause_setter=pause_calls.append)
    assert poller.state["paused"] is False
    poller.handle_command("PAUSE")
    assert poller.state["paused"] is True
    assert pause_calls == [True]
    poller.handle_command("RESUME")
    assert poller.state["paused"] is False
    assert pause_calls == [True, False]


def test_kill_triggers_on_kill_callback():
    fired = {"n": 0}

    def on_kill():
        fired["n"] += 1

    poller = _poller(on_kill=on_kill)
    msg = poller.handle_command("KILL")
    assert fired["n"] == 1
    assert poller.state["kill_requested"] is True
    assert "Shutdown" in msg


def test_status_command_calls_provider():
    poller = _poller(status_provider=lambda: {
        "mode": "PAPER", "day_pnl": 1234.56, "day_trades": 2, "healthy": True,
    })
    msg = poller.handle_command("STATUS")
    assert "PAPER" in msg
    assert "1234.56" in msg
    assert "trades=2" in msg
    assert "healthy=True" in msg


def test_positions_command_empty():
    poller = _poller(positions_provider=lambda: {"positions": {}})
    assert "No open positions" in poller.handle_command("POSITIONS")


def test_unknown_returns_help_listing_commands():
    poller = _poller()
    msg = poller.handle_command("UNKNOWN")
    for cmd in ["/status", "/positions", "/pnl", "/pause", "/resume", "/kill"]:
        assert cmd in msg


def test_pnl_command_with_daily_summary_shape():
    poller = _poller(pnl_provider=lambda: {
        "trade_count": 3, "gross_pnl": 4500.0, "net_pnl": 4200.5,
        "max_drawdown_money": 800.0,
    })
    msg = poller.handle_command("PNL")
    assert "trades=3" in msg
    assert "gross=4500" in msg
    assert "net=4200" in msg


# --------------------------------------------------------- security


def test_unauthorized_sender_dropped():
    """A message from a chat_id other than the configured one must NOT
    invoke any command handler."""
    fired = {"kill": 0}
    poller = _poller(on_kill=lambda: fired.__setitem__("kill", fired["kill"] + 1))

    update = {
        "update_id": 1,
        "message": {
            "chat": {"id": 999999},  # not the authorized 555
            "from": {"username": "attacker"},
            "text": "/kill",
        },
    }
    poller._handle_update(update)
    assert fired["kill"] == 0


def test_authorized_sender_dispatched():
    fired = {"kill": 0}
    poller = _poller(on_kill=lambda: fired.__setitem__("kill", fired["kill"] + 1))

    # Telegram returns chat.id as int; the poller stringifies for comparison.
    update = {
        "update_id": 1,
        "message": {"chat": {"id": 555}, "text": "/kill"},
    }
    poller._handle_update(update)
    assert fired["kill"] == 1


# --------------------------------------------------------- offset


def test_offset_advances_after_each_update():
    poller = _poller()
    for uid in [10, 11, 15]:
        poller._handle_update({
            "update_id": uid,
            "message": {"chat": {"id": 555}, "text": "/status"},
        })
    # The next poll uses offset = last + 1, so it must not replay these.
    assert poller._last_update_id == 15


def test_offset_advances_even_for_unauthorized_sender():
    """If we didn't advance the offset for ignored messages, an
    attacker could keep `/kill` perpetually in the next-batch by never
    being acked."""
    poller = _poller()
    poller._handle_update({
        "update_id": 42,
        "message": {"chat": {"id": 999}, "text": "/kill"},
    })
    assert poller._last_update_id == 42


# --------------------------------------------------------- poll loop


def test_poll_once_calls_getUpdates_with_offset():
    session = _MockSession([(200, {"ok": True, "result": []})])
    poller = _poller(http_session=session)
    poller._last_update_id = 7
    poller._poll_once()
    assert session.calls
    assert session.calls[0]["params"]["offset"] == 8


def test_poll_once_recovers_from_network_error():
    """A RequestException is logged and the loop continues."""
    import requests as _r

    session = _MockSession([_r.ConnectionError("boom")])
    poller = _poller(http_session=session)
    # Must not raise.
    poller._poll_once()


def test_poll_once_recovers_from_non_200():
    session = _MockSession([(503, "service unavailable")])
    poller = _poller(http_session=session)
    poller._poll_once()  # Must not raise.


def test_disabled_when_no_bot_token():
    """`start()` must not spawn a thread when there's no token."""
    poller = TelegramPoller(bot=TelegramBot(bot_token="", chat_id=""))
    poller.start()
    assert poller._thread is None
