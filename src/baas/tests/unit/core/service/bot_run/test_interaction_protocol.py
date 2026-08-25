from __future__ import annotations

from copy import deepcopy

import pytest

from secbaas.community.api.bot_interaction import InteractionResolution
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
        "kind": "exec",
        "expiresAtMs": 123,
        "options": [
            {"label": "Once", "decision": "allow-once"},
            {"label": "Deny", "value": "deny"},
            {"label": "Once again", "decision": "allow-once"},
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


def test_requested_event_skips_bad_options_and_preserves_source_payload() -> None:
    payload = {
        "interactionId": "int-mixed",
        "kind": "exec",
        "command": "make test",
        "options": [
            "not-an-object",
            {"label": "Once", "decision": "", "value": "allow-once"},
            {"label": "Deny", "decision": 42, "value": "deny"},
            {"decision": "hidden"},
            {"label": "Once again", "decision": "allow-once", "value": "ignored"},
            {},
        ],
    }
    original = deepcopy(payload)

    event = EngineInteractionRequestedEvent.from_payload(
        session_key="trusted-session",
        payload=payload,
    )

    assert event.allowed_decisions == ("allow-once", "deny")
    assert event.envelope["payload"] is payload
    assert payload == original


def test_exec_without_options_uses_bcn_default_allowed_decisions() -> None:
    event = EngineInteractionRequestedEvent.from_payload(
        session_key="trusted-session",
        payload={
            "interactionId": "int-defaults",
            "kind": "exec",
            "command": "pwd",
        },
    )

    assert event.allowed_decisions == ("allow-once", "allow-always", "deny")


def test_exec_with_explicit_null_options_does_not_use_defaults() -> None:
    event = EngineInteractionRequestedEvent.from_payload(
        session_key="trusted-session",
        payload={
            "interactionId": "int-null",
            "kind": "exec",
            "command": "pwd",
            "options": None,
        },
    )

    assert event.allowed_decisions == ()


def test_ask_user_uses_fixed_actions_and_ignores_top_level_options() -> None:
    event = EngineInteractionRequestedEvent.from_payload(
        session_key="trusted-session",
        payload={
            "interactionId": "int-ask",
            "kind": "ask_user",
            "questions": [{"header": "Name", "question": "Your name?"}],
            "options": [{"label": "Hidden", "decision": "hidden"}],
        },
    )

    assert event.allowed_decisions == ("submit", "cancel")


@pytest.mark.parametrize(
    "payload",
    [
        {"interactionId": "int-mode", "kind": "mode_switch"},
        {"interactionId": "int-mode", "kind": "mode_switch", "options": None},
        {"interactionId": "int-mode", "kind": "mode_switch", "options": {}},
        {
            "interactionId": "int-mode",
            "kind": "mode_switch",
            "options": [{"decision": "hidden-without-label"}],
        },
        {"interactionId": "int-unknown", "kind": "future_kind"},
        {"interactionId": "int-missing-kind"},
    ],
)
def test_non_exec_without_visible_options_is_fail_closed(payload) -> None:
    event = EngineInteractionRequestedEvent.from_payload(
        session_key="trusted-session",
        payload=payload,
    )

    assert event.allowed_decisions == ()


@pytest.mark.parametrize(
    "payload, message",
    [
        (
            {"interactionId": "int-1", "kind": "exec", "expiresAtMs": "soon"},
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


def test_mode_transition_resolved_preserves_raw_event_name() -> None:
    payload = {
        "sessionKey": "payload-session",
        "interactionId": "int-mode",
        "transitionId": "int-mode",
        "kind": "mode_switch",
        "phase": "proceeded",
        "decision": "proceed",
        "seq": 131,
    }

    event = EngineInteractionResolvedEvent.from_mode_transition_payload(
        session_key="trusted-session",
        payload=payload,
    )

    assert event.session_key == "trusted-session"
    assert event.interaction_id == "int-mode"
    assert event.envelope == {
        "type": "event",
        "event": "mode_transition.resolved",
        "payload": payload,
        "seq": 131,
    }


def test_mode_transition_resolved_rejects_non_mode_kind() -> None:
    with pytest.raises(ValueError, match="kind must be mode_switch"):
        EngineInteractionResolvedEvent.from_mode_transition_payload(
            session_key="s-1",
            payload={"interactionId": "int-1", "kind": "exec"},
        )


def test_engine_exchange_validates_and_preserves_exact_frames() -> None:
    request = build_interaction_resolve_request(
        request_id="engine-1",
        interaction_id="int-1",
        resolution=InteractionResolution(kind="exec", decision="allow-once"),
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
        resolution=InteractionResolution(kind="exec", decision="deny"),
    )

    with pytest.raises(ValueError, match="does not match"):
        EngineInteractionResolveExchange.from_frames(
            request=request,
            response={"type": "res", "id": "engine-2", "ok": True},
        )


def test_engine_exchange_rejects_non_res_response_type() -> None:
    request = build_interaction_resolve_request(
        request_id="engine-1",
        interaction_id="int-1",
        resolution=InteractionResolution(kind="exec", decision="allow-once"),
    )
    with pytest.raises(ValueError, match="response type must be res"):
        EngineInteractionResolveExchange.from_frames(
            request=request,
            response={"type": "event", "id": "engine-1", "ok": True},
        )


def test_engine_exchange_rejects_non_boolean_ok() -> None:
    request = build_interaction_resolve_request(
        request_id="engine-1",
        interaction_id="int-1",
        resolution=InteractionResolution(kind="exec", decision="allow-once"),
    )
    with pytest.raises(ValueError, match="ok must be boolean"):
        EngineInteractionResolveExchange.from_frames(
            request=request,
            response={"type": "res", "id": "engine-1", "ok": "true"},
        )


def test_engine_exchange_rejects_request_without_id() -> None:
    with pytest.raises(ValueError, match="missing id"):
        EngineInteractionResolveExchange.from_frames(
            request={"type": "req", "method": "interaction.resolve", "params": {}},
            response={"type": "res", "id": "engine-1", "ok": True},
        )


@pytest.mark.parametrize(
    "error, expected",
    [
        ({"message": "boom"}, "boom"),
        ({"detail": "x"}, '{"detail":"x"}'),
        ("plain error", "plain error"),
        (None, "engine rejected interaction.resolve"),
    ],
)
def test_engine_exchange_captures_error_message_shapes(error, expected) -> None:
    request = build_interaction_resolve_request(
        request_id="engine-1",
        interaction_id="int-1",
        resolution=InteractionResolution(kind="exec", decision="deny"),
    )
    exchange = EngineInteractionResolveExchange.from_frames(
        request=request,
        response={"type": "res", "id": "engine-1", "ok": False, "error": error},
    )
    assert exchange.accepted is False
    assert exchange.error_message == expected


def test_build_resolve_request_rejects_empty_identity() -> None:
    with pytest.raises(ValueError, match="missing request id"):
        build_interaction_resolve_request(
            request_id="",
            interaction_id="int-1",
            resolution=InteractionResolution(kind="exec", decision="allow-once"),
        )
    with pytest.raises(ValueError, match="missing interactionId"):
        build_interaction_resolve_request(
            request_id="engine-1",
            interaction_id="",
            resolution=InteractionResolution(kind="exec", decision="allow-once"),
        )


def test_build_ask_user_submit_request_preserves_complete_answer() -> None:
    request = build_interaction_resolve_request(
        request_id="engine-ask-1",
        interaction_id="int-ask-1",
        resolution=InteractionResolution(
            kind="ask_user",
            decision="submit",
            answer="deploy_target: staging；components: web，worker",
            message="deploy_target: staging；components: web，worker",
            values={
                "deploy_target": "staging",
                "components": "web，worker",
            },
            answers={
                "what's your deploy target?": "staging",
                "whats' the components?": "web，worker",
            },
            selected_options=(("staging",), ("web", "worker")),
        ),
    )

    assert request == {
        "type": "req",
        "id": "engine-ask-1",
        "method": "interaction.resolve",
        "params": {
            "interactionId": "int-ask-1",
            "decision": "submit",
            "answer": "deploy_target: staging；components: web，worker",
            "message": "deploy_target: staging；components: web，worker",
            "values": {
                "deploy_target": "staging",
                "components": "web，worker",
            },
            "answers": {
                "what's your deploy target?": "staging",
                "whats' the components?": "web，worker",
            },
            "selectedOptions": [["staging"], ["web", "worker"]],
        },
    }


def test_build_ask_user_submit_request_uses_synthetic_other_for_custom_input() -> None:
    request = build_interaction_resolve_request(
        request_id="engine-ask-custom",
        interaction_id="int-ask-custom",
        resolution=InteractionResolution(
            kind="ask_user",
            decision="submit",
            answer="Components: web，自定义输入: scheduler",
            message="Components: web，自定义输入: scheduler",
            values={"Components": "web，自定义输入: scheduler"},
            answers={
                "Which components?": "web，自定义输入: scheduler",
            },
            selected_options=(("other",),),
        ),
    )

    assert request == {
        "type": "req",
        "id": "engine-ask-custom",
        "method": "interaction.resolve",
        "params": {
            "interactionId": "int-ask-custom",
            "decision": "submit",
            "answer": "Components: web，自定义输入: scheduler",
            "message": "Components: web，自定义输入: scheduler",
            "values": {"Components": "web，自定义输入: scheduler"},
            "answers": {
                "Which components?": "web，自定义输入: scheduler",
            },
            "selectedOptions": [["other"]],
        },
    }


def test_build_ask_user_submit_request_preserves_skipped_values() -> None:
    request = build_interaction_resolve_request(
        request_id="engine-ask-skip",
        interaction_id="int-ask-skip",
        resolution=InteractionResolution(
            kind="ask_user",
            decision="submit",
            answer="Array: ；Empty: ；Blank:    ",
            message="Array: ；Empty: ；Blank:    ",
            values={"Array": "", "Empty": "", "Blank": "   "},
            answers={
                "Skip with an empty array?": "",
                "Skip with an empty string?": "",
                "Skip with whitespace?": "   ",
            },
            selected_options=((), ("",), ("   ",)),
        ),
    )

    assert request["params"]["values"] == {
        "Array": "",
        "Empty": "",
        "Blank": "   ",
    }
    assert request["params"]["answers"] == {
        "Skip with an empty array?": "",
        "Skip with an empty string?": "",
        "Skip with whitespace?": "   ",
    }
    assert request["params"]["selectedOptions"] == [[], [""], ["   "]]


def test_build_ask_user_cancel_request_omits_answer_fields() -> None:
    request = build_interaction_resolve_request(
        request_id="engine-ask-cancel",
        interaction_id="int-ask-1",
        resolution=InteractionResolution(kind="ask_user", decision="cancel"),
    )

    assert request["method"] == "interaction.resolve"
    assert request["params"] == {
        "interactionId": "int-ask-1",
        "decision": "cancel",
    }


@pytest.mark.parametrize("decision", ["allow-once", "deny"])
def test_build_exec_request_uses_interaction_resolve(decision: str) -> None:
    request = build_interaction_resolve_request(
        request_id="engine-exec-1",
        interaction_id="int-exec-1",
        resolution=InteractionResolution(kind="exec", decision=decision),
    )

    assert request["method"] == "interaction.resolve"
    assert request["params"] == {
        "interactionId": "int-exec-1",
        "decision": decision,
    }


@pytest.mark.parametrize("decision", ["proceed", "stay"])
def test_build_mode_switch_request_uses_transition_rpc(decision: str) -> None:
    request = build_interaction_resolve_request(
        request_id="engine-mode-1",
        interaction_id="int-mode-1",
        resolution=InteractionResolution(kind="mode_switch", decision=decision),
    )

    assert request == {
        "type": "req",
        "id": "engine-mode-1",
        "method": "mode_transition.resolve",
        "params": {
            "transitionId": "int-mode-1",
            "decision": decision,
        },
    }


def test_build_legacy_request_uses_interaction_resolve() -> None:
    request = build_interaction_resolve_request(
        request_id="engine-legacy-1",
        interaction_id="int-legacy-1",
        resolution=InteractionResolution(decision="legacy-decision"),
    )

    assert request["method"] == "interaction.resolve"
    assert request["params"] == {
        "interactionId": "int-legacy-1",
        "decision": "legacy-decision",
    }


def test_resolved_event_falls_back_to_id_when_interaction_id_absent() -> None:
    event = EngineInteractionResolvedEvent.from_payload(
        session_key="s-1", payload={"id": "fallback-id"}
    )
    assert event.interaction_id == "fallback-id"


def test_resolved_event_rejects_missing_interaction_identity() -> None:
    with pytest.raises(ValueError, match="missing interactionId"):
        EngineInteractionResolvedEvent.from_payload(session_key="s-1", payload={})


def test_resolved_event_rejects_empty_session_key() -> None:
    with pytest.raises(ValueError, match="missing sessionKey"):
        EngineInteractionResolvedEvent.from_payload(
            session_key="", payload={"interactionId": "int-1"}
        )


def test_allowed_decisions_skips_option_with_label_but_no_value() -> None:
    event = EngineInteractionRequestedEvent.from_payload(
        session_key="s-1",
        payload={
            "interactionId": "int-skip",
            "kind": "exec",
            "options": [
                {"label": "No Value"},
                {"label": "Once", "decision": "allow-once"},
            ],
        },
    )
    assert event.allowed_decisions == ("allow-once",)
