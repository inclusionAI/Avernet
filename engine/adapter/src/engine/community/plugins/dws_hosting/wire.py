"""Strict projection of DWS v1.0.62 outputs, without localized error parsing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from engine.community.kernel.dws_connection import (
    AuthorizationStatus,
    ConnectionHealth,
    ConnectionIdentity,
    DwsError,
    HistoryResult,
    MessageActivity,
)
from engine.community.kernel.dws_hosting import DirectMessage

DM_EVENT = "user_im_message_receive_o2o_all"


def object_json(raw: str) -> dict:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise DwsError("invalid_output") from None


def payload(raw: str) -> dict:
    value = object_json(raw)
    if "outcome" in value:
        if value.get("ok") is not True or value["outcome"] != "success":
            raise DwsError("incomplete_result")
        value = value.get("data")
    if not isinstance(value, dict):
        raise DwsError("invalid_output")
    return value


def identifier(value) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise DwsError("invalid_identity")
    return value


def auth_status(raw: str, expected: ConnectionIdentity) -> AuthorizationStatus:
    data = payload(raw)
    if data.get("success") is not True or type(data.get("authenticated")) is not bool:
        raise DwsError("invalid_auth_status")
    if not data["authenticated"]:
        return AuthorizationStatus(authenticated=False)
    if (data.get("corp_id"), data.get("user_id")) != (
        expected.organization_id,
        expected.user_id,
    ):
        raise DwsError("identity_mismatch")
    return AuthorizationStatus(
        authenticated=True,
        organization_id=expected.organization_id,
        user_id=expected.user_id,
    )


def health(raw: str, consumer_pid: int) -> ConnectionHealth:
    data = payload(raw)
    bus = data.get("bus")
    if not isinstance(bus, dict):
        raise DwsError("invalid_health")
    live = bus.get("live")
    if not isinstance(live, dict):
        return ConnectionHealth(connected=False, generation="absent")
    try:
        source = live["source_state"]
        consumers = [x for x in live["consumers"] if x["pid"] == consumer_pid]
        generation = json.dumps(
            [
                live["bus"]["pid"],
                source["reconnect_count"],
                [x["dropped"] for x in consumers],
            ]
        )
        return ConnectionHealth(
            connected=source["state"] == "connected" and bool(consumers),
            generation=generation,
        )
    except (KeyError, TypeError):
        raise DwsError("invalid_health") from None


def discovery(raw: str) -> tuple[str, ...]:
    data = payload(raw)
    if data.get("complete") is not True or data.get("unknownTypeCount") != 0:
        raise DwsError("incomplete_discovery")
    rows = data.get("conversations")
    if not isinstance(rows, list) or len(rows) > 10_000:
        raise DwsError("invalid_discovery")
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get("type") not in ("direct", "group"):
            raise DwsError("invalid_discovery")
        if row["type"] == "direct":
            result.append(identifier(row.get("conversationId")))
    return tuple(dict.fromkeys(result))


def timestamp(value, naive_timezone: str) -> int:
    if type(value) is int and value >= 0:
        return value if value >= 1_000_000_000_000 else value * 1000
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(naive_timezone))
        return int(parsed.timestamp() * 1000)
    except (ValueError, AttributeError, TypeError):
        raise DwsError("invalid_message_time") from None


def rfc3339(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="seconds")


def history(
    raw: str,
    conversation_id: str,
    start_ms: int,
    end_ms: int,
    identity: ConnectionIdentity,
    naive_timezone: str,
) -> HistoryResult:
    data = payload(raw)
    contract = data.get("contractVersion")
    if contract not in (None, "im.message-list.v1"):
        raise DwsError("unsupported_history_contract")
    if (
        data.get("complete") is not True
        or data.get("hasMore") is not False
        or data.get("paginationKnown") is not True
        or data.get("failedCount") != 0
        or data.get("truncated") is not False
    ):
        raise DwsError("incomplete_history")
    rows = data.get("messages")
    if not isinstance(rows, list) or len(rows) > 5000:
        raise DwsError("invalid_history")
    messages, observed, unsupported = [], [], 0
    activities, activity_complete = [], True
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("conversationId", conversation_id) != conversation_id
        ):
            raise DwsError("history_scope_mismatch")
        created = timestamp(row.get("createTime"), naive_timezone)
        if not start_ms <= created < end_ms:
            raise DwsError("history_range_mismatch")
        mid = identifier(row.get("messageId"))
        observed.append(mid)
        # COSEC: retain canonical identity even when the body is not supported.
        # Media can be a human reply; an AI-marked message is not human takeover.
        try:
            sender = identifier(row.get("senderId"))
        except DwsError:
            sender = None
            activity_complete = False
        if sender == identity.self_open_id:
            sender = identity.user_id
        if sender is not None:
            activities.append(
                MessageActivity(
                    conversation_id=conversation_id,
                    message_id=mid,
                    sender_id=sender,
                    created_at_ms=created,
                    ai_generated=bool(row.get("messageAiSendFlag")),
                )
            )
        # DWS v1 preserves messageType only when the underlying IM API supplies it.
        # COSEC: accept its untyped text projection, but not resource/forward
        # summaries or AI-client messages (whose marker can be "DWS", not bool).
        text_type = row.get("messageType") == "text" or (
            contract == "im.message-list.v1" and "messageType" not in row
        )
        if (
            not text_type
            or row.get("resourceRefs")
            or row.get("forwarded")
            or row.get("messageAiSendFlag")
        ):
            unsupported += 1
            continue
        sender = identifier(sender)
        try:
            messages.append(
                DirectMessage(
                    conversation_id=conversation_id,
                    message_id=mid,
                    sender_id=sender,
                    sender_name=row.get("sender")
                    if isinstance(row.get("sender"), str)
                    else "",
                    text=row.get("text"),
                    created_at_ms=created,
                )
            )
        except ValidationError:
            raise DwsError("unsupported_message") from None
    return HistoryResult(
        messages=tuple(messages),
        observed_ids=tuple(observed),
        unsupported_count=unsupported,
        activities=tuple(activities),
        activity_complete=activity_complete,
    )
