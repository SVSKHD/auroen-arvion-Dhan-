"""Binary packet decoding for the Dhan v2 market feed.

The layout `<BHBIfI` (16 bytes) is taken verbatim from the official
DhanHQ-py SDK (marketfeed.py:process_ticker). If Dhan changes the layout
this test will turn red and we will know.

We craft a fixture packet for a ticker carrying LTP=19820.50 on segment 2
(NSE_FNO) for security_id 49081, time 1736046900 (2025-01-05 09:15:00 IST).
"""

import struct

from brokers.dhan.market_feed import (
    SERVER_DISCONNECT_CODE,
    TICKER_FMT,
    TICKER_RESPONSE_CODE,
    is_server_disconnect,
    parse_ticker,
)


def _build_ticker(segment: int, security_id: int, ltp: float, ltt: int) -> bytes:
    return struct.pack(TICKER_FMT, TICKER_RESPONSE_CODE, 16, segment, security_id, ltp, ltt)


def test_ticker_packet_round_trips():
    fixture = _build_ticker(segment=2, security_id=49081, ltp=19820.50, ltt=1736046900)
    assert len(fixture) == 16

    tick = parse_ticker(fixture)
    assert tick is not None
    assert tick.response_code == TICKER_RESPONSE_CODE
    assert tick.exchange_segment == 2
    assert tick.security_id == 49081
    assert tick.ltp == 19820.5
    assert tick.ltt_epoch == 1736046900


def test_known_hex_fixture_decodes_correctly():
    """A captured hex string equivalent to the round-trip above.

    The hex below is what struct.pack('<BHBIfI', 2, 16, 2, 49081, 19820.5,
    1736046900).hex() produces — kept as a literal here so a regression in
    parse_ticker shows up against a *constant* fixture, not a value we
    generated in the same test.
    """
    hex_fixture = "02100002b9bf000000d99a4634f97967"
    tick = parse_ticker(bytes.fromhex(hex_fixture))
    assert tick is not None
    assert tick.exchange_segment == 2
    assert tick.security_id == 49081
    assert round(tick.ltp, 2) == 19820.5


def test_parse_ticker_returns_none_for_non_ticker_code():
    # First byte = 4 = Quote packet, parse_ticker must return None.
    packet = struct.pack(TICKER_FMT, 4, 16, 2, 49081, 100.0, 1736046900)
    assert parse_ticker(packet) is None


def test_parse_ticker_returns_none_for_truncated_packet():
    assert parse_ticker(b"\x02\x10\x00") is None


def test_server_disconnect_detected():
    packet = bytes([SERVER_DISCONNECT_CODE]) + b"\x00" * 15
    assert is_server_disconnect(packet)


def test_server_disconnect_negative():
    packet = bytes([TICKER_RESPONSE_CODE]) + b"\x00" * 15
    assert not is_server_disconnect(packet)
