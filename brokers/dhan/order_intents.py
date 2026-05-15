"""Crash-safe order intents.

Required because a process death between "I decided to place this order" and
"the broker confirmed an order ID" leaves the system in a state where, on
restart, it cannot tell whether the order was actually placed. Without
intents, restart would re-submit the order — double-placing it.

Lifecycle:
    1. Caller mints a deterministic intent_id (e.g.
       "NIFTY-2026-01-05-LONG-STOP") and calls record_pending(intent_id,
       payload). State is persisted to disk *before* the HTTP call.
    2. Caller invokes the broker API. On success, record_submitted(intent_id,
       order_id). On exception, the intent stays PENDING.
    3. On restart, reconcile_pending() walks every PENDING intent. If it has
       an order_id (we got past step 2 but crashed before status update), we
       query the broker for the live status. If it has no order_id, the
       caller decides whether to retry or skip — but the safe default is to
       re-issue with the same intent_id and check whether the broker has it.
    4. Order updates (from get_order polling or webhook) flow through
       record_status() to mark FILLED / CANCELLED / REJECTED.

State shape on disk (per StateStore):

    {"order_intents": {
        "<intent_id>": {
            "intent_id": str,
            "status": "PENDING"|"SUBMITTED"|"FILLED"|"CANCELLED"|"REJECTED",
            "order_id": str | null,
            "payload": dict,
            "created_at": iso8601,
            "updated_at": iso8601
        }
    }}
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from core.state_store import StateStore
from core.time_utils import now_ist

INTENTS_KEY = "order_intents"

Status = str  # one of: PENDING, SUBMITTED, FILLED, CANCELLED, REJECTED


@dataclass
class OrderIntent:
    intent_id: str
    status: Status
    payload: dict[str, Any]
    order_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: now_ist().isoformat())
    updated_at: str = field(default_factory=lambda: now_ist().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "OrderIntent":
        return cls(
            intent_id=raw["intent_id"],
            status=raw["status"],
            payload=raw.get("payload", {}),
            order_id=raw.get("order_id"),
            created_at=raw.get("created_at", now_ist().isoformat()),
            updated_at=raw.get("updated_at", now_ist().isoformat()),
        )


class OrderIntentStore:
    def __init__(self, state_store: StateStore) -> None:
        self.state_store = state_store

    def get(self, intent_id: str) -> Optional[OrderIntent]:
        raw = self._load_all().get(intent_id)
        return OrderIntent.from_dict(raw) if raw else None

    def all_pending(self) -> list[OrderIntent]:
        return [
            OrderIntent.from_dict(raw)
            for raw in self._load_all().values()
            if raw.get("status") in {"PENDING", "SUBMITTED"}
        ]

    def record_pending(self, intent_id: str, payload: dict[str, Any]) -> OrderIntent:
        """Persist BEFORE the broker call. If the process dies after this
        write, restart can see we intended to place this order and act.
        """
        intents = self._load_all()
        existing = intents.get(intent_id)
        if existing is not None:
            return OrderIntent.from_dict(existing)

        intent = OrderIntent(intent_id=intent_id, status="PENDING", payload=payload)
        intents[intent_id] = intent.to_dict()
        self._save_all(intents)
        return intent

    def record_submitted(self, intent_id: str, order_id: str) -> OrderIntent:
        return self._update(intent_id, status="SUBMITTED", order_id=order_id)

    def record_status(self, intent_id: str, status: Status) -> OrderIntent:
        return self._update(intent_id, status=status)

    def _update(
        self,
        intent_id: str,
        status: Status,
        order_id: Optional[str] = None,
    ) -> OrderIntent:
        intents = self._load_all()
        raw = intents.get(intent_id)
        if raw is None:
            raise KeyError(f"Unknown intent_id: {intent_id}")
        raw["status"] = status
        if order_id is not None:
            raw["order_id"] = order_id
        raw["updated_at"] = now_ist().isoformat()
        intents[intent_id] = raw
        self._save_all(intents)
        return OrderIntent.from_dict(raw)

    def _load_all(self) -> dict[str, dict[str, Any]]:
        payload = self.state_store.load()
        return dict(payload.get(INTENTS_KEY, {}) or {})

    def _save_all(self, intents: dict[str, dict[str, Any]]) -> None:
        payload = self.state_store.load()
        payload[INTENTS_KEY] = intents
        self.state_store.save(payload)
