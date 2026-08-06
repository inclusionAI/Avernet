from __future__ import annotations

import pytest

from secbaas.community.core.service.bot_run._interaction_protocol import (
    EngineInteractionRequestedEvent,
    EngineInteractionResolvedEvent,
    EngineInteractionResolveExchange,
    build_interaction_resolve_request,
)


def test_requested_event_is_parsed_once_into_explicit_fields() -> None:
    payload = {
        "sessionKey": "payload-session",
        "interactionId": "int-1",
        "expiresAtMs": 123,
        "options": [
            {"decision": "allow-once"},
            {"value": "deny"},
            {"decision": "allow-once"},
        ],
        "seq": 9,
    }

    event = EngineInteractionRequestedEvent.from_payload(
        session_key="trusted-session",
        payload=payload,
    )

    assert event.session_key == "trusted-session"
    assert event.interaction_id == "int-1"
    assert event.allowed_decisions == ("allow-once", "deny")
    assert event.expires_at_ms == 123
    assert event.envelope["payload"] is payload
    assert event.envelope["seq"] == 9


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"interactionId": "int-1", "options": {}}, "options must be an array"),
        (
            {"interactionId": "int-1", "options": [{}]},
            "option is missing decision",
        ),
        (
            {"interactionId": "int-1", "expiresAtMs": "soon"},
            "expiresAtMs must be an integer",
        ),
    ],
)
def test_requested_event_rejects_malformed_dispatch_metadata(payload, message) -> None:
    with pytest.raises(ValueError, match=message):
        EngineInteractionRequestedEvent.from_payload(session_key="s-1", payload=payload)


def test_resolved_event_uses_explicit_session_identity() -> None:
    payload = {
        "sessionKey": "payload-session",
        "interactionId": "int-1",
        "decision": "allow-once",
    }

    event = EngineInteractionResolvedEvent.from_payload(
        session_key="trusted-session",
        payload=payload,
    )

    assert event.session_key == "trusted-session"
    assert event.interaction_id == "int-1"
    assert event.envelope == {
        "type": "event",
        "event": "interaction.resolved",
        "payload": payload,
    }


def test_engine_exchange_validates_and_preserves_exact_frames() -> None:
    request = build_interaction_resolve_request(
        request_id="engine-1",
        interaction_id="int-1",
        decision="allow-once",
    )
    response = {"type": "res", "id": "engine-1", "ok": True}

    exchange = EngineInteractionResolveExchange.from_frames(
        request=request,
        response=response,
    )

    assert exchange.request is request
    assert exchange.response is response
    assert exchange.accepted is True
    assert exchange.error_message is None


def test_engine_exchange_rejects_mismatched_response_id() -> None:
    request = build_interaction_resolve_request(
        request_id="engine-1",
        interaction_id="int-1",
        decision="deny",
    )

    with pytest.raises(ValueError, match="does not match"):
        EngineInteractionResolveExchange.from_frames(
            request=request,
            response={"type": "res", "id": "engine-2", "ok": True},
        )
