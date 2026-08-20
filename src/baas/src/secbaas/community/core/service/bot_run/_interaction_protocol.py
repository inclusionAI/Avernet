"""Typed protocol boundary for engine human-interaction frames."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from secbaas.community.api.bot_interaction import InteractionResolution

JsonObject = dict[str, object]
InteractionEventName = Literal[
    "interaction.requested",
    "interaction.resolved",
    "mode_transition.resolved",
]

_DEFAULT_EXEC_DECISIONS = ("allow-once", "allow-always", "deny")


@dataclass(frozen=True, slots=True)
class EngineInteractionRequestedEvent:
    """Validated engine request ready for persistence and SSE delivery."""

    session_key: str
    interaction_id: str
    envelope: JsonObject
    allowed_decisions: tuple[str, ...]
    expires_at_ms: int | None

    @classmethod
    def from_payload(
        cls,
        *,
        session_key: str,
        payload: JsonObject,
    ) -> EngineInteractionRequestedEvent:
        return cls(
            session_key=_required_identity(session_key, "sessionKey"),
            interaction_id=_interaction_id(payload),
            envelope=_event_envelope("interaction.requested", payload),
            allowed_decisions=_allowed_decisions(payload),
            expires_at_ms=_optional_int(payload, "expiresAtMs"),
        )


@dataclass(frozen=True, slots=True)
class EngineInteractionResolvedEvent:
    """Validated terminal engine event ready for persistence and SSE delivery."""

    session_key: str
    interaction_id: str
    envelope: JsonObject

    @classmethod
    def from_payload(
        cls,
        *,
        session_key: str,
        payload: JsonObject,
    ) -> EngineInteractionResolvedEvent:
        return cls(
            session_key=_required_identity(session_key, "sessionKey"),
            interaction_id=_interaction_id(payload),
            envelope=_event_envelope("interaction.resolved", payload),
        )

    @classmethod
    def from_mode_transition_payload(
        cls,
        *,
        session_key: str,
        payload: JsonObject,
    ) -> EngineInteractionResolvedEvent:
        if payload.get("kind") != "mode_switch":
            raise ValueError("mode transition resolved kind must be mode_switch")
        return cls(
            session_key=_required_identity(session_key, "sessionKey"),
            interaction_id=_interaction_id(payload),
            envelope=_event_envelope("mode_transition.resolved", payload),
        )


@dataclass(frozen=True, slots=True)
class EngineInteractionResolveExchange:
    """Validated engine RPC request/response pair."""

    request: JsonObject
    response: JsonObject
    accepted: bool
    error_message: str | None

    @classmethod
    def from_frames(
        cls,
        *,
        request: JsonObject,
        response: JsonObject,
    ) -> EngineInteractionResolveExchange:
        request_id = _required_str(request, "id")
        if response.get("type") != "res":
            raise ValueError("interaction.resolve response type must be res")
        if _required_str(response, "id") != request_id:
            raise ValueError("interaction.resolve response id does not match request")
        ok = response.get("ok")
        if not isinstance(ok, bool):
            raise ValueError("interaction.resolve response ok must be boolean")
        return cls(
            request=request,
            response=response,
            accepted=ok,
            error_message=None if ok else _error_message(response.get("error")),
        )


def build_interaction_resolve_request(
    *,
    request_id: str,
    interaction_id: str,
    resolution: InteractionResolution,
) -> JsonObject:
    """Build the exact engine request frame sent over websocket."""
    identity_key = (
        "transitionId" if resolution.kind == "mode_switch" else "interactionId"
    )
    method = (
        "mode_transition.resolve"
        if resolution.kind == "mode_switch"
        else "interaction.resolve"
    )
    params: JsonObject = {
        identity_key: _required_identity(interaction_id, identity_key),
        "decision": resolution.decision,
    }
    if resolution.kind == "ask_user":
        if resolution.answer is not None:
            params["answer"] = resolution.answer
        if resolution.message is not None:
            params["message"] = resolution.message
        if resolution.values is not None:
            params["values"] = dict(resolution.values)
        if resolution.answers is not None:
            params["answers"] = dict(resolution.answers)
        if resolution.selected_options is not None:
            params["selectedOptions"] = [
                list(options) for options in resolution.selected_options
            ]
    return {
        "type": "req",
        "id": _required_identity(request_id, "request id"),
        "method": method,
        "params": params,
    }


def _interaction_id(payload: JsonObject) -> str:
    value = payload.get("interactionId")
    if value is None:
        value = payload.get("id")
    if not isinstance(value, str) or not value:
        raise ValueError("interaction event is missing interactionId")
    return value


def _event_envelope(event: InteractionEventName, payload: JsonObject) -> JsonObject:
    envelope: JsonObject = {"type": "event", "event": event, "payload": payload}
    if "seq" in payload:
        envelope["seq"] = payload["seq"]
    return envelope


def _allowed_decisions(payload: JsonObject) -> tuple[str, ...]:
    kind = payload.get("kind")
    if kind == "ask_user":
        return ("submit", "cancel")
    if kind not in {"exec", "mode_switch"}:
        return ()

    if "options" not in payload:
        if kind == "exec":
            return _DEFAULT_EXEC_DECISIONS
        return ()

    options = payload.get("options")
    if options is None:
        return ()
    if not isinstance(options, list):
        return ()

    decisions: list[str] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        if _non_empty_str(option.get("label")) is None:
            continue
        value = _non_empty_str(option.get("decision")) or _non_empty_str(
            option.get("value")
        )
        if value is None:
            continue
        if value not in decisions:
            decisions.append(value)
    return tuple(decisions)


def _non_empty_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _optional_int(payload: JsonObject, key: str) -> int | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"interaction {key} must be an integer")
    return value


def _required_str(value: JsonObject, key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"interaction frame is missing {key}")
    return item


def _required_identity(value: str, name: str) -> str:
    if not value:
        raise ValueError(f"interaction event is missing {name}")
    return value


def _error_message(value: object) -> str:
    if isinstance(value, dict):
        message = value.get("message")
        if isinstance(message, str) and message:
            return message
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str) and value:
        return value
    return "engine rejected interaction.resolve"
