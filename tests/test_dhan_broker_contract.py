"""Contract tests for `DhanBroker` against a mocked HTTP session.

Each test asserts that a given `BrokerAdapter` method calls the right
verb + path with the right JSON payload field names — those names are
copied from the official DhanHQ-py SDK, so if they drift we want to know
in CI, not in production.

The mock `requests.Session` records every call and returns canned JSON
responses. We do not exercise the network at all.
"""

import json
from typing import Any, Optional

import pytest

from brokers.base import Side, StopOrderRequest
from brokers.dhan.dhan_broker import DhanBroker, StaticIpNotWhitelisted
from brokers.dhan.dhan_client import DhanAuthError, DhanClient
from brokers.dhan.order_intents import OrderIntentStore
from core.state_store import StateStore


class _Response:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class _Sequential:
    """Wraps a queue of responses returned in order across successive calls
    to the same route. Used distinctly from raw list bodies (e.g. /positions
    can legitimately return a JSON array).
    """

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)


class _MockSession:
    def __init__(self, route_table: dict[tuple[str, str], Any]) -> None:
        # route_table key: (METHOD, PATH). value can be:
        #   - a dict  -> response body, status 200
        #   - a list  -> response body (JSON array), status 200
        #   - (status, body) tuple -> exact status
        #   - _Sequential([entry, entry, ...]) for retry-style scenarios
        self.route_table = route_table
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        path = url.replace("https://api.dhan.co/v2", "")
        self.calls.append({"method": method, "path": path, "json": json, "headers": headers})

        key = (method.upper(), path)
        entry = self.route_table.get(key)
        if entry is None:
            return _Response(404, {"error": f"unrouted {method} {path}"})
        if isinstance(entry, _Sequential):
            value = entry.responses.pop(0) if entry.responses else (200, {})
        else:
            value = entry
        if isinstance(value, tuple):
            status, body = value
        else:
            status, body = 200, value
        return _Response(status, body)


def _broker(routes: dict[tuple[str, str], Any], tmp_path) -> tuple[DhanBroker, _MockSession]:
    session = _MockSession(routes)
    client = DhanClient(
        client_id="1100000001",
        access_token="abc.def.ghi",
        session=session,
        sleep=lambda _s: None,
    )
    store = StateStore.__new__(StateStore)
    store.path = tmp_path / "order_intents.json"
    return DhanBroker(client=client, intent_store=OrderIntentStore(store)), session


# --------------------------------------------------------------------- account


def test_get_account_hits_fundlimit(tmp_path):
    routes = {
        ("GET", "/fundlimit"): {
            "dhanClientId": "1100000001",
            "availabelBalance": 250000.5,
            "utilizedAmount": 12500.0,
        }
    }
    broker, session = _broker(routes, tmp_path)
    account = broker.get_account()

    assert session.calls[0] == {
        "method": "GET",
        "path": "/fundlimit",
        "json": None,
        "headers": session.calls[0]["headers"],
    }
    headers = session.calls[0]["headers"]
    assert headers["access-token"] == "abc.def.ghi"
    assert headers["client-id"] == "1100000001"
    assert account.available_balance == pytest.approx(250000.5)
    assert account.used_margin == pytest.approx(12500.0)


def test_get_ltp_for_security_posts_marketfeed_ltp(tmp_path):
    routes = {
        ("POST", "/marketfeed/ltp"): {
            "data": {"NSE_FNO": {"49081": {"last_price": 19850.25}}},
            "status": "success",
        }
    }
    broker, session = _broker(routes, tmp_path)
    ltp = broker.get_ltp_for_security("NSE_FNO", "49081")

    assert ltp == pytest.approx(19850.25)
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["path"] == "/marketfeed/ltp"
    body = session.calls[0]["json"]
    assert body["NSE_FNO"] == [49081]
    assert body["dhanClientId"] == "1100000001"


# ------------------------------------------------------------------- writes


def test_place_market_order_payload_shape(tmp_path):
    routes = {
        ("POST", "/orders"): {"orderId": "ord-123", "orderStatus": "PENDING"},
    }
    broker, session = _broker(routes, tmp_path)

    order = broker.place_market_order(
        symbol="NIFTY",
        side="LONG",
        quantity=50,
        intent_id="NIFTY-2026-01-05-LONG-MKT",
        security_id="49081",
        exchange_segment="NSE_FNO",
        product_type="INTRADAY",
    )

    body = session.calls[0]["json"]
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["path"] == "/orders"
    assert body["transactionType"] == "BUY"
    assert body["orderType"] == "MARKET"
    assert body["exchangeSegment"] == "NSE_FNO"
    assert body["productType"] == "INTRADAY"
    assert body["securityId"] == "49081"
    assert body["quantity"] == 50
    assert body["triggerPrice"] == 0.0
    assert body["price"] == 0.0
    assert body["validity"] == "DAY"
    assert body["dhanClientId"] == "1100000001"
    assert order.order_id == "ord-123"


def test_place_stop_order_uses_stop_loss_market(tmp_path):
    routes = {("POST", "/orders"): {"orderId": "ord-stop", "orderStatus": "PENDING"}}
    broker, session = _broker(routes, tmp_path)

    request = StopOrderRequest(
        symbol="NIFTY",
        security_id="49081",
        side="LONG",
        quantity=50,
        trigger_price=19820.0,
        exchange_segment="NSE_FNO",
        product_type="INTRADAY",
    )
    broker.place_stop_order(request, intent_id="NIFTY-2026-01-05-LONG-STOP")

    body = session.calls[0]["json"]
    assert body["orderType"] == "STOP_LOSS_MARKET"
    assert body["triggerPrice"] == 19820.0
    assert body["transactionType"] == "BUY"


def test_cancel_order_hits_delete(tmp_path):
    routes = {("DELETE", "/orders/ord-123"): {"orderId": "ord-123", "orderStatus": "CANCELLED"}}
    broker, session = _broker(routes, tmp_path)
    assert broker.cancel_order("ord-123") is True
    assert session.calls[0]["method"] == "DELETE"
    assert session.calls[0]["path"] == "/orders/ord-123"


def test_modify_sl_puts_to_orders_with_trigger_price(tmp_path):
    routes = {
        ("GET", "/orders/ord-stop"): {
            "orderId": "ord-stop",
            "orderType": "STOP_LOSS_MARKET",
            "legName": "ENTRY_LEG",
            "quantity": 50,
            "price": 0.0,
            "triggerPrice": 19820.0,
            "validity": "DAY",
            "orderStatus": "PENDING",
            "transactionType": "BUY",
        },
        ("PUT", "/orders/ord-stop"): {"orderId": "ord-stop", "orderStatus": "PENDING"},
    }
    broker, session = _broker(routes, tmp_path)

    assert broker.modify_sl("ord-stop", new_sl=19900.0) is True
    # First call is GET to fetch shape; second is PUT.
    put_call = session.calls[1]
    assert put_call["method"] == "PUT"
    assert put_call["path"] == "/orders/ord-stop"
    body = put_call["json"]
    assert body["orderId"] == "ord-stop"
    assert body["triggerPrice"] == 19900.0
    assert body["orderType"] == "STOP_LOSS_MARKET"


def test_get_open_positions_filters_zero_qty(tmp_path):
    routes = {
        ("GET", "/positions"): [
            {"securityId": "49081", "tradingSymbol": "NIFTY", "netQty": 50, "buyAvg": 19820.0},
            {"securityId": "49082", "tradingSymbol": "BANKNIFTY", "netQty": 0, "buyAvg": 0.0},
            {"securityId": "49083", "tradingSymbol": "MCX_GOLD", "netQty": -1, "sellAvg": 70200.0},
        ]
    }
    broker, _ = _broker(routes, tmp_path)
    positions = broker.get_open_positions()
    assert len(positions) == 2
    nifty = next(p for p in positions if p.symbol == "NIFTY")
    assert nifty.side == "LONG"
    assert nifty.quantity == 50
    mcx = next(p for p in positions if p.symbol == "MCX_GOLD")
    assert mcx.side == "SHORT"
    assert mcx.quantity == 1


def test_close_position_places_opposite_market_order(tmp_path):
    routes = {
        ("GET", "/positions"): [
            {"securityId": "49081", "tradingSymbol": "NIFTY", "netQty": 50, "buyAvg": 19820.0}
        ],
        ("POST", "/orders"): {"orderId": "squareoff-1", "orderStatus": "PENDING"},
    }
    broker, session = _broker(routes, tmp_path)
    assert broker.close_position("49081") is True
    post_body = session.calls[-1]["json"]
    assert post_body["transactionType"] == "SELL"
    assert post_body["orderType"] == "MARKET"
    assert post_body["quantity"] == 50


# --------------------------------------------------------------- auth + IP


def test_401_raises_dhan_auth_error(tmp_path):
    routes = {("GET", "/fundlimit"): (401, {"errorMessage": "token expired"})}
    broker, _ = _broker(routes, tmp_path)
    with pytest.raises(DhanAuthError):
        broker.get_account()


def test_assert_can_trade_refuses_when_not_whitelisted(tmp_path):
    routes = {
        ("GET", "/staticip"): {"dhanClientWhitelisted": False, "dhanClientId": "1100000001"},
    }
    broker, _ = _broker(routes, tmp_path)
    with pytest.raises(StaticIpNotWhitelisted):
        broker.assert_can_trade()


def test_assert_can_trade_passes_on_whitelisted(tmp_path):
    routes = {
        ("GET", "/staticip"): {"dhanClientWhitelisted": True, "dhanClientId": "1100000001"},
        ("GET", "/fundlimit"): {"dhanClientId": "1100000001", "availabelBalance": 100000.0},
    }
    broker, _ = _broker(routes, tmp_path)
    broker.assert_can_trade()


def test_5xx_retried_with_backoff(tmp_path):
    # First call 503, second 200.
    routes = {("GET", "/fundlimit"): _Sequential([(503, {"err": "down"}), (200, {"availabelBalance": 5000.0})])}
    broker, session = _broker(routes, tmp_path)
    account = broker.get_account()
    assert account.available_balance == pytest.approx(5000.0)
    assert len(session.calls) == 2
