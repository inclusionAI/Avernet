"""Bot run interaction records and persisted payload contract."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

InteractionState = Literal[
    "requested",
    "queued",
    "dispatching",
    "resolved",
    "expired",
    "failed",
]
JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class BotRunInteractionPayloadPatch:
    """Fields that may be appended after the requested row is created.

    Initial request metadata is intentionally absent: a later transition must
    never replace the original requested snapshot, allowed decisions, or
    deadline.
    """

    decision: str | None = None
    client_req: JsonObject | None = None
    resolution: JsonObject | None = None
    idempotency_key: str | None = None
    engine_req: JsonObject | None = None
    engine_res: JsonObject | None = None
    resolved: JsonObject | None = None
    dispatch_error: str | None = None
    expire_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BotRunInteractionPayload:
    """Validated representation of the interaction table's ``payload`` JSON.

    Protocol frames are opaque snapshots. Only the few fields used by the
    state machine are represented explicitly, so callers never inspect nested
    request/response dictionaries to recover identity or dispatch input.
    """

    requested: JsonObject
    allowed_decisions: tuple[str, ...] | None = None
    expires_at_ms: int | None = None
    decision: str | None = None
    client_req: JsonObject | None = None
    resolution: JsonObject | None = None
    idempotency_key: str | None = None
    engine_req: JsonObject | None = None
    engine_res: JsonObject | None = None
    resolved: JsonObject | None = None
    dispatch_error: str | None = None
    expire_reason: str | None = None

    @classmethod
    def from_dict(cls, value: JsonObject) -> BotRunInteractionPayload:
        """Decode persisted JSON strictly instead of hiding corrupt rows."""
        return cls(
            requested=_required_object(value, "requested"),
            allowed_decisions=_optional_str_tuple(value, "allowedDecisions"),
            expires_at_ms=_optional_int(value, "expiresAtMs"),
            decision=_optional_str(value, "decision"),
            client_req=_optional_object(value, "clientReq"),
            resolution=_optional_object(value, "resolution"),
            idempotency_key=_optional_str(value, "idempotencyKey"),
            engine_req=_optional_object(value, "engineReq"),
            engine_res=_optional_object(value, "engineRes"),
            resolved=_optional_object(value, "resolved"),
            dispatch_error=_optional_str(value, "dispatchError"),
            expire_reason=_optional_str(value, "expireReason"),
        )

    def to_dict(self) -> JsonObject:
        values: JsonObject = {
            "requested": self.requested,
            "allowedDecisions": (
                list(self.allowed_decisions)
                if self.allowed_decisions is not None
                else None
            ),
            "expiresAtMs": self.expires_at_ms,
            "decision": self.decision,
            "clientReq": self.client_req,
            "resolution": self.resolution,
            "idempotencyKey": self.idempotency_key,
            "engineReq": self.engine_req,
            "engineRes": self.engine_res,
            "resolved": self.resolved,
            "dispatchError": self.dispatch_error,
            "expireReason": self.expire_reason,
        }
        return {key: item for key, item in values.items() if item is not None}

    def merge(self, patch: BotRunInteractionPayloadPatch) -> BotRunInteractionPayload:
        changes = {
            field: item
            for field, item in (
                ("decision", patch.decision),
                ("client_req", patch.client_req),
                ("resolution", patch.resolution),
                ("idempotency_key", patch.idempotency_key),
                ("engine_req", patch.engine_req),
                ("engine_res", patch.engine_res),
                ("resolved", patch.resolved),
                ("dispatch_error", patch.dispatch_error),
                ("expire_reason", patch.expire_reason),
            )
            if item is not None
        }
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class BotRunInteractionRecord:
    """``baas_bot_run_interaction`` row."""

    id: int
    session_key: str
    interaction_id: str
    state: InteractionState
    payload: BotRunInteractionPayload
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BotRunInteractionCreateResult:
    record: BotRunInteractionRecord
    created: bool


def _required_object(value: JsonObject, key: str) -> JsonObject:
    item = value.get(key)
    if not isinstance(item, dict):
        raise ValueError(f"interaction payload {key} must be an object")
    return item


def _optional_object(value: JsonObject, key: str) -> JsonObject | None:
    if key not in value:
        return None
    item = value[key]
    if not isinstance(item, dict):
        raise ValueError(f"interaction payload {key} must be an object")
    return item


def _optional_str(value: JsonObject, key: str) -> str | None:
    if key not in value:
        return None
    item = value[key]
    if not isinstance(item, str) or not item:
        raise ValueError(f"interaction payload {key} must be a non-empty string")
    return item


def _optional_int(value: JsonObject, key: str) -> int | None:
    if key not in value:
        return None
    item = value[key]
    if not isinstance(item, int) or isinstance(item, bool):
        raise ValueError(f"interaction payload {key} must be an integer")
    return item


def _optional_str_tuple(value: JsonObject, key: str) -> tuple[str, ...] | None:
    if key not in value:
        return None
    item = value[key]
    if not isinstance(item, list) or any(
        not isinstance(decision, str) or not decision for decision in item
    ):
        raise ValueError(
            f"interaction payload {key} must be an array of non-empty strings"
        )
    return tuple(item)
