"""Telegram polling daemon command dispatch.

We don't hit the real Telegram API; we exercise `handle_command` and
`parse_command` and the state-flag side effects.
"""

from core.telegram_bot import TelegramBot, TelegramPoller


def test_parse_command_recognizes_supported_set():
    p = TelegramBot.parse_command
    assert p("/status") == "STATUS"
    assert p("/positions") == "POSITIONS"
    assert p("/pnl") == "PNL"
    assert p("/pause") == "PAUSE"
    assert p("/resume") == "RESUME"
    assert p("/kill") == "KILL"
    assert p("/restart") == "RESTART"
    assert p("/garbage") == "UNKNOWN"


def test_pause_resume_toggles_state_flag():
    poller = TelegramPoller(bot=TelegramBot(bot_token="", chat_id=""))
    assert poller.state["paused"] is False
    msg = poller.handle_command("PAUSE")
    assert poller.state["paused"] is True
    assert "Paused" in msg

    msg = poller.handle_command("RESUME")
    assert poller.state["paused"] is False
    assert "Resumed" in msg


def test_kill_triggers_on_kill_callback():
    triggered = {"count": 0}

    def on_kill():
        triggered["count"] += 1

    poller = TelegramPoller(bot=TelegramBot(bot_token="", chat_id=""), on_kill=on_kill)
    msg = poller.handle_command("KILL")
    assert triggered["count"] == 1
    assert poller.state["kill_requested"] is True
    assert "Shutdown" in msg


def test_status_pulls_from_provider():
    snapshot = {"mode": "PAPER", "day_pnl": 1234.56, "day_trades": 2}
    poller = TelegramPoller(
        bot=TelegramBot(bot_token="", chat_id=""),
        status_provider=lambda: snapshot,
    )
    msg = poller.handle_command("STATUS")
    assert "PAPER" in msg
    assert "1234.56" in msg
    assert "trades=2" in msg


def test_positions_command_with_empty_book():
    poller = TelegramPoller(
        bot=TelegramBot(bot_token="", chat_id=""),
        status_provider=lambda: {"positions": {}},
    )
    assert "No open positions" in poller.handle_command("POSITIONS")


def test_unknown_command_returns_help():
    poller = TelegramPoller(bot=TelegramBot(bot_token="", chat_id=""))
    msg = poller.handle_command("UNKNOWN")
    assert "/status" in msg


def test_poller_no_op_when_bot_token_missing():
    """`start()` must not spawn a thread when there's no token, so
    dev environments without secrets don't get noisy threads."""
    poller = TelegramPoller(bot=TelegramBot(bot_token="", chat_id=""))
    poller.start()
    assert poller._thread is None
