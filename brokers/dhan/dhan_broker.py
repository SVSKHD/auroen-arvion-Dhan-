"""DhanBroker — concrete `BrokerAdapter` over Dhan REST v2.

Payload field names and endpoint paths verified against the official
DhanHQ-py SDK (dhan-oss/DhanHQ-py: `src/dhanhq/_order.py`, `_funds.py`,
`_portfolio.py`, `_market_feed.py`). Keep this comment honest — if the
upstream renames a field, update here, not in callers.

Idempotency: every order-placing method routes through OrderIntentStore.
Callers pass an explicit `intent_id` (e.g. "NIFTY-2026-01-05-LONG-STOP").
The intent is persisted in PENDING state *before* the HTTP call; on
broker confirmation it moves to SUBMITTED with the broker order_id.
After a crash, `reconcile_pending_intents()` queries the broker for any
intent that has an order_id but is still in a non-terminal state.

Static IP check: Dhan v2 requires the calling host's egress to be on the
account's whitelist. `assert_can_trade()` calls /staticip + /fundlimit at
startup so we refuse to trade rather than fail every order.
"""

from typing import Any, Optional

from brokers.base import (
    BrokerAccount,
    BrokerAdapter,
    BrokerOrder,
    BrokerPosition,
    Side,
    StopOrderRequest,
)
from brokers.dhan.dhan_client import DhanAuthError, DhanClient, DhanHttpError
from brokers.dhan.order_intents import OrderIntent, OrderIntentStore
from core.logger import get_logger
from core.state_store import StateStore

logger = get_logger("dhan_broker")


_TRANSACTION_BY_SIDE = {"LONG": "BUY", "SHORT": "SELL"}
_OPPOSITE_TRANSACTION = {"LONG": "SELL", "SHORT": "BUY"}


class StaticIpNotWhitelisted(RuntimeError):
    pass


class DhanBroker(BrokerAdapter):
    def __init__(
        self,
        client: Optional[DhanClient] = None,
        intent_store: Optional[OrderIntentStore] = None,
    ) -> None:
        self.client = client or DhanClient()
        self.intent_store = intent_store or OrderIntentStore(
            StateStore("order_intents.json")
        )

    # ------------------------------------------------------------------ startup

    def assert_can_trade(self) -> None:
        """Run the two pre-trading checks the Dhan platform enforces.

        Failing either of these means orders will be rejected anyway —
        better to abort startup loudly than to discover it on every
        order placement.
        """
        try:
            staticip = self.client.get("/staticip")
        except DhanAuthError:
            raise
        except DhanHttpError as exc:
            logger.error("Static IP check failed: %s", exc)
            raise StaticIpNotWhitelisted(str(exc)) from exc

        if isinstance(staticip, dict) and staticip.get("dhanClientWhitelisted") is False:
            raise StaticIpNotWhitelisted(
                f"Static IP not whitelisted for client; response={staticip}"
            )

        # Touch /fundlimit so an expired access token surfaces here, not on
        # the first order placement.
        self.client.get("/fundlimit")

    # ------------------------------------------------------------------- reads

    def get_account(self) -> BrokerAccount:
        raw = self.client.get("/fundlimit")
        return BrokerAccount(
            client_id=str(raw.get("dhanClientId") or self.client.client_id),
            available_balance=float(raw.get("availabelBalance", 0.0) or 0.0),
            net_balance=_opt_float(raw.get("availabelBalance")),
            used_margin=_opt_float(raw.get("utilizedAmount")),
            raw=raw if isinstance(raw, dict) else None,
        )

    def get_ltp(self, symbol: str) -> float:
        raise NotImplementedError(
            "DhanBroker.get_ltp requires segment + security_id, not a bare symbol. "
            "Call get_ltp_for_security() or feed via DhanMarketFeed."
        )

    def get_ltp_for_security(self, exchange_segment: str, security_id: str) -> float:
        """The Dhan REST LTP endpoint takes a {segment: [security_id, ...]}
        dict, not a symbol string. See `_market_feed.py` in DhanHQ-py.
        """
        payload = {exchange_segment: [int(security_id)]}
        raw = self.client.post("/marketfeed/ltp", payload)
        data = (raw or {}).get("data", raw) if isinstance(raw, dict) else {}
        entries = (data or {}).get(exchange_segment, {}) if isinstance(data, dict) else {}
        record = entries.get(str(security_id)) if isinstance(entries, dict) else None
        if record is None:
            raise DhanHttpError(0, f"LTP missing for {exchange_segment}/{security_id}: {raw}")
        return float(record["last_price"] if "last_price" in record else record["ltp"])

    def get_order(self, order_id: str) -> BrokerOrder:
        raw = self.client.get(f"/orders/{order_id}")
        return _broker_order_from_dhan(raw if isinstance(raw, dict) else {})

    def get_open_orders(self, symbol: Optional[str] = None) -> list[BrokerOrder]:
        raw = self.client.get("/orders")
        rows = raw if isinstance(raw, list) else (raw or {}).get("data") or []
        orders = [_broker_order_from_dhan(r) for r in rows if isinstance(r, dict)]
        if symbol is not None:
            orders = [o for o in orders if (o.raw or {}).get("tradingSymbol") == symbol]
        return [o for o in orders if o.status in {"PENDING", "OPEN", "PARTIALLY_FILLED"}]

    def get_open_position(self, symbol: str) -> Optional[BrokerPosition]:
        for p in self.get_open_positions():
            if p.symbol == symbol:
                return p
        return None

    def get_open_positions(self) -> list[BrokerPosition]:
        raw = self.client.get("/positions")
        rows = raw if isinstance(raw, list) else (raw or {}).get("data") or []
        out: list[BrokerPosition] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            net_qty = int(r.get("netQty", 0) or 0)
            if net_qty == 0:
                continue
            side: Side = "LONG" if net_qty > 0 else "SHORT"
            out.append(
                BrokerPosition(
                    position_id=str(r.get("securityId", "")),
                    symbol=str(r.get("tradingSymbol") or r.get("securityId", "")),
                    side=side,
                    entry_price=float(r.get("buyAvg") if side == "LONG" else r.get("sellAvg") or 0.0),
                    quantity=abs(net_qty),
                    pnl=_opt_float(r.get("realizedProfit") or r.get("unrealizedProfit")),
                    raw=r,
                )
            )
        return out

    # ------------------------------------------------------------------ writes

    def place_market_order(
        self,
        symbol: str,
        side: Side,
        quantity: int,
        intent_id: Optional[str] = None,
        security_id: Optional[str] = None,
        exchange_segment: str = "NSE_FNO",
        product_type: str = "INTRADAY",
    ) -> BrokerOrder:
        if security_id is None:
            raise ValueError("Dhan requires an explicit security_id; symbol alone is insufficient")
        payload = {
            "transactionType": _TRANSACTION_BY_SIDE[side],
            "exchangeSegment": exchange_segment.upper(),
            "productType": product_type.upper(),
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": str(security_id),
            "quantity": int(quantity),
            "disclosedQuantity": 0,
            "price": 0.0,
            "triggerPrice": 0.0,
            "afterMarketOrder": False,
        }
        return self._place_with_intent(intent_id or _default_intent(symbol, side, "MKT"), payload)

    def place_stop_order(self, request: StopOrderRequest, intent_id: Optional[str] = None) -> BrokerOrder:
        payload = {
            "transactionType": _TRANSACTION_BY_SIDE[request.side],
            "exchangeSegment": request.exchange_segment.upper(),
            "productType": request.product_type.upper(),
            "orderType": "STOP_LOSS_MARKET",
            "validity": "DAY",
            "securityId": str(request.security_id),
            "quantity": int(request.quantity),
            "disclosedQuantity": 0,
            "price": 0.0,
            "triggerPrice": float(request.trigger_price),
            "afterMarketOrder": False,
        }
        return self._place_with_intent(
            intent_id or _default_intent(request.symbol, request.side, "STP"),
            payload,
        )

    def cancel_order(self, order_id: str) -> bool:
        try:
            self.client.delete(f"/orders/{order_id}")
            return True
        except DhanHttpError as exc:
            logger.warning("Cancel failed for order_id=%s: %s", order_id, exc)
            return False

    def modify_sl(self, position_id: str, new_sl: float) -> bool:
        """Modifies a resting stop order's trigger price. The caller passes
        the *order* id of the SL leg here, despite the parameter being
        named position_id by the abstract base — Dhan modifies by orderId.
        """
        return self._modify_trigger(order_id=position_id, new_trigger=new_sl)

    def modify_tp(self, position_id: str, new_tp: float) -> bool:
        return self._modify_trigger(order_id=position_id, new_trigger=new_tp)

    def close_position(self, position_id: str) -> bool:
        """Square-off via opposite-side market order. position_id is the
        Dhan securityId of the open position.
        """
        position = next(
            (p for p in self.get_open_positions() if p.position_id == position_id),
            None,
        )
        if position is None:
            logger.info("close_position called but no open position for %s", position_id)
            return True
        opposite_side: Side = "SHORT" if position.side == "LONG" else "LONG"
        try:
            self.place_market_order(
                symbol=position.symbol,
                side=opposite_side,
                quantity=position.quantity,
                security_id=position_id,
                intent_id=f"{position_id}-squareoff-{position.side}",
            )
            return True
        except DhanHttpError as exc:
            logger.error("Square-off failed for %s: %s", position_id, exc)
            return False

    # ----------------------------------------------------------- idempotency

    def _place_with_intent(self, intent_id: str, payload: dict[str, Any]) -> BrokerOrder:
        """Persist PENDING intent before HTTP call; promote to SUBMITTED on
        broker ack. If we crash between record_pending and the HTTP call,
        the intent stays PENDING and reconcile_pending_intents() can
        re-issue. If we crash between HTTP success and record_submitted,
        the intent still has no order_id and the reconciler will refuse to
        re-issue blindly (it logs and skips so the operator can inspect).
        """
        existing = self.intent_store.get(intent_id)
        if existing is not None and existing.order_id:
            logger.info(
                "Intent %s already submitted to broker (order_id=%s); returning cached",
                intent_id, existing.order_id,
            )
            return self.get_order(existing.order_id)

        self.intent_store.record_pending(intent_id, payload)

        raw = self.client.post("/orders", payload)
        order = _broker_order_from_dhan(raw if isinstance(raw, dict) else {}, payload=payload)
        self.intent_store.record_submitted(intent_id, order.order_id)
        return order

    def reconcile_pending_intents(self) -> list[OrderIntent]:
        """Walk PENDING/SUBMITTED intents from disk after restart. For
        intents that have an order_id, refresh status from /orders/{id}.
        For intents with NO order_id, leave them PENDING and return them
        — the operator (or the orchestrator's startup hook) must decide
        whether to re-issue.
        """
        out: list[OrderIntent] = []
        for intent in self.intent_store.all_pending():
            if intent.order_id is None:
                logger.warning(
                    "Intent %s persisted PENDING but never got an order_id — "
                    "manual decision required",
                    intent.intent_id,
                )
                out.append(intent)
                continue
            try:
                broker_order = self.get_order(intent.order_id)
            except DhanHttpError as exc:
                logger.error(
                    "Reconcile failed for intent %s order_id %s: %s",
                    intent.intent_id, intent.order_id, exc,
                )
                out.append(intent)
                continue
            self.intent_store.record_status(intent.intent_id, broker_order.status)
        return out

    def _modify_trigger(self, order_id: str, new_trigger: float) -> bool:
        order = self.get_order(order_id)
        payload = {
            "orderId": str(order_id),
            "orderType": (order.raw or {}).get("orderType", "STOP_LOSS_MARKET"),
            "legName": (order.raw or {}).get("legName", "ENTRY_LEG"),
            "quantity": int(order.quantity),
            "price": float(order.price or 0.0),
            "disclosedQuantity": int((order.raw or {}).get("disclosedQuantity", 0) or 0),
            "triggerPrice": float(new_trigger),
            "validity": (order.raw or {}).get("validity", "DAY"),
        }
        try:
            self.client.put(f"/orders/{order_id}", payload)
            return True
        except DhanHttpError as exc:
            logger.warning("Modify failed for order_id=%s: %s", order_id, exc)
            return False


# --------------------------------------------------------------- conversions


_STATUS_MAP = {
    "PENDING": "PENDING",
    "TRANSIT": "PENDING",
    "TRADED": "FILLED",
    "FILLED": "FILLED",
    "PART_TRADED": "PARTIALLY_FILLED",
    "PARTIALLY_FILLED": "PARTIALLY_FILLED",
    "REJECTED": "REJECTED",
    "CANCELLED": "CANCELLED",
    "CANCELED": "CANCELLED",
    "EXPIRED": "CANCELLED",
}

_KIND_MAP = {
    "MARKET": "MARKET",
    "LIMIT": "LIMIT",
    "STOP_LOSS": "STOP_LIMIT",
    "STOP_LOSS_MARKET": "STOP",
}


def _broker_order_from_dhan(raw: dict[str, Any], payload: Optional[dict[str, Any]] = None) -> BrokerOrder:
    side_raw = raw.get("transactionType") or (payload or {}).get("transactionType") or "BUY"
    side: Side = "LONG" if side_raw == "BUY" else "SHORT"

    status_raw = (raw.get("orderStatus") or "PENDING").upper()
    kind_raw = (raw.get("orderType") or (payload or {}).get("orderType") or "MARKET").upper()

    return BrokerOrder(
        order_id=str(raw.get("orderId") or raw.get("order_id") or ""),
        side=side,
        status=_STATUS_MAP.get(status_raw, "UNKNOWN"),
        price=float(raw.get("price") or (payload or {}).get("price") or 0.0),
        quantity=int(raw.get("quantity") or (payload or {}).get("quantity") or 0),
        order_kind=_KIND_MAP.get(kind_raw, "MARKET"),
        trigger_price=_opt_float(raw.get("triggerPrice") or (payload or {}).get("triggerPrice")),
        raw=raw,
    )


def _opt_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _default_intent(symbol: str, side: Side, suffix: str) -> str:
    from core.time_utils import now_ist

    return f"{symbol}-{now_ist().date().isoformat()}-{side}-{suffix}"
