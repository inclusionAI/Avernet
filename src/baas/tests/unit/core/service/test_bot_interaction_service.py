from __future__ import annotations

import time
from dataclasses import replace

import pytest

from secbaas.community.api.bot_interaction import (
    InteractionConflictError,
    InteractionResolution,
)
from secbaas.community.core.repository.bot_run_interaction import (
    BotRunInteractionCreateResult,
    BotRunInteractionPayload,
    BotRunInteractionPayloadPatch,
    BotRunInteractionRecord,
)


def test_interaction_resolution_round_trips_ask_user_payload() -> None:
    resolution = InteractionResolution(
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
    )

    payload = resolution.to_dict()

    assert payload == {
        "kind": "ask_user",
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
    }
    assert InteractionResolution.from_dict(payload) == resolution


def test_interaction_resolution_round_trips_skipped_ask_user_values() -> None:
    resolution = InteractionResolution(
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
    )

    payload = resolution.to_dict()

    assert payload["selectedOptions"] == [[], [""], ["   "]]
    assert InteractionResolution.from_dict(payload) == resolution


def test_interaction_resolution_omits_absent_optional_fields() -> None:
    resolution = InteractionResolution(kind="exec", decision="deny")

    assert resolution.to_dict() == {"kind": "exec", "decision": "deny"}


from secbaas.community.core.service.bot_interaction import (
    DefaultBotInteractionService,
    InteractionBadRequestError,
    InteractionDispatch,
)
from secbaas.community.core.service.bot_run._interaction_protocol import (
    EngineInteractionRequestedEvent,
)

BAAS_INTERACTION_ID = "BAAS-INTERACTION-f6489adf6a916257a4b9a56bd056bf58"


class FakeRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], BotRunInteractionRecord] = {}
        self.next_id = 1

    def create_requested(
        self,
        *,
        baas_interaction_id: str,
        session_key: str,
        interaction_id: str,
        payload: BotRunInteractionPayload,
    ) -> BotRunInteractionCreateResult:
        key = (session_key, interaction_id)
        existing = self.rows.get(key)
        if existing is not None:
            return BotRunInteractionCreateResult(existing, created=False)
        record = BotRunInteractionRecord(
            id=self.next_id,
            baas_interaction_id=baas_interaction_id,
            session_key=session_key,
            interaction_id=interaction_id,
            state="requested",
            payload=payload,
        )
        self.rows[key] = record
        self.next_id += 1
        return BotRunInteractionCreateResult(record, created=True)

    def get(
        self, *, session_key: str, interaction_id: str
    ) -> BotRunInteractionRecord | None:
        return self.rows.get((session_key, interaction_id))

    def get_by_baas_interaction_id(
        self, *, baas_interaction_id: str
    ) -> BotRunInteractionRecord | None:
        return next(
            (
                record
                for record in self.rows.values()
                if record.baas_interaction_id == baas_interaction_id
            ),
            None,
        )

    def transition(
        self,
        *,
        session_key,
        interaction_id,
        from_states,
        to_state,
        patch,
    ):
        key = (session_key, interaction_id)
        record = self.rows.get(key)
        if record is None or record.state not in from_states:
            return None
        updated = replace(
            record,
            state=to_state,
            payload=record.payload.merge(patch),
        )
        self.rows[key] = updated
        return updated

    def transition_by_baas_interaction_id(
        self,
        *,
        baas_interaction_id,
        from_states,
        to_state,
        patch,
    ):
        record = self.get_by_baas_interaction_id(
            baas_interaction_id=baas_interaction_id
        )
        if record is None:
            return None
        return self.transition(
            session_key=record.session_key,
            interaction_id=record.interaction_id,
            from_states=from_states,
            to_state=to_state,
            patch=patch,
        )

    def merge_payload(
        self,
        *,
        session_key,
        interaction_id,
        allowed_states,
        patch,
    ):
        key = (session_key, interaction_id)
        record = self.rows.get(key)
        if record is None or record.state not in allowed_states:
            return None
        updated = replace(record, payload=record.payload.merge(patch))
        self.rows[key] = updated
        return updated


def _requested_envelope(*, session_key: str = "payload-session") -> dict:
    return {
        "type": "event",
        "event": "interaction.requested",
        "payload": {
            "sessionKey": session_key,
            "interactionId": "int-1",
            "expiresAtMs": int(time.time() * 1000) + 60_000,
            "options": [
                {"decision": "allow-once", "label": "Allow once"},
                {"decision": "deny", "label": "Deny"},
            ],
        },
    }


def _record_requested(repo: FakeRepo, service: DefaultBotInteractionService) -> None:
    created = service.record_requested(
        session_key="s-1",
        interaction_id="int-1",
        envelope=_requested_envelope(),
        allowed_decisions=("allow-once", "deny"),
        expires_at_ms=int(time.time() * 1000) + 60_000,
    )
    assert created.created is True


def _resolve(
    service: DefaultBotInteractionService,
    *,
    decision: str = "allow-once",
    resolution: InteractionResolution | None = None,
    idempotency_key: str | None = None,
):
    resolution = resolution or InteractionResolution(decision=decision)
    request = {
        "type": "req",
        "id": "client-1",
        "method": "interaction.resolve",
        "params": {
            "sessionKey": "s-1",
            "interactionId": BAAS_INTERACTION_ID,
            "decision": resolution.decision,
        },
    }
    if idempotency_key is not None:
        request["params"]["idempotencyKey"] = idempotency_key
    return service.resolve(
        baas_interaction_id=BAAS_INTERACTION_ID,
        resolution=resolution,
        request_envelope=request,
        idempotency_key=idempotency_key,
    )


def test_record_requested_uses_explicit_identity_not_envelope_identity() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)

    service.record_requested(
        session_key="trusted-session",
        interaction_id="trusted-interaction",
        envelope=_requested_envelope(session_key="untrusted-payload-session"),
        allowed_decisions=("allow-once",),
        expires_at_ms=None,
    )

    assert (
        repo.get(session_key="trusted-session", interaction_id="trusted-interaction")
        is not None
    )
    assert (
        repo.get(session_key="untrusted-payload-session", interaction_id="int-1")
        is None
    )


def test_record_requested_returns_deterministic_baas_interaction_id() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)

    result = service.record_requested(
        session_key="s-1",
        interaction_id="int-1",
        envelope=_requested_envelope(),
        allowed_decisions=("allow-once",),
        expires_at_ms=None,
    )

    assert result.baas_interaction_id == BAAS_INTERACTION_ID
    assert result.created is True


def test_same_engine_interaction_id_in_another_session_gets_another_baas_id() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)

    first = service.record_requested(
        session_key="s-1",
        interaction_id="int-1",
        envelope=_requested_envelope(),
        allowed_decisions=("allow-once",),
        expires_at_ms=None,
    )
    second = service.record_requested(
        session_key="s-2",
        interaction_id="int-1",
        envelope=_requested_envelope(),
        allowed_decisions=("allow-once",),
        expires_at_ms=None,
    )

    assert first.baas_interaction_id == BAAS_INTERACTION_ID
    assert second.baas_interaction_id == (
        "BAAS-INTERACTION-41bca94003b2c4897af521fa6ae9b376"
    )


def test_redelivered_request_returns_persisted_legacy_public_id() -> None:
    repo = FakeRepo()
    repo.rows[("s-1", "int-1")] = BotRunInteractionRecord(
        id=1,
        baas_interaction_id="legacy-engine-id-exposed-before-migration",
        session_key="s-1",
        interaction_id="int-1",
        state="requested",
        payload=BotRunInteractionPayload(requested=_requested_envelope()),
    )
    service = DefaultBotInteractionService(repo)

    result = service.record_requested(
        session_key="s-1",
        interaction_id="int-1",
        envelope=_requested_envelope(),
        allowed_decisions=("allow-once",),
        expires_at_ms=None,
    )

    assert result.baas_interaction_id == ("legacy-engine-id-exposed-before-migration")
    assert result.created is False


def test_duplicate_requested_is_idempotent() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    kwargs = {
        "session_key": "s-1",
        "interaction_id": "int-1",
        "envelope": _requested_envelope(),
        "allowed_decisions": ("allow-once", "deny"),
        "expires_at_ms": None,
    }

    assert service.record_requested(**kwargs).created is True
    assert service.record_requested(**kwargs).created is False
    assert len(repo.rows) == 1


def test_http_resolve_moves_requested_to_queued() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    _record_requested(repo, service)

    result = _resolve(service)

    assert result.interaction_id == BAAS_INTERACTION_ID
    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "queued"
    assert record.payload.decision == "allow-once"
    assert record.payload.resolution == {"decision": "allow-once"}
    assert record.payload.client_req is not None
    assert record.payload.client_req["id"] == "client-1"


def test_provider_resolve_persists_complete_resolution_and_idempotency() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    _record_requested(repo, service)
    resolution = InteractionResolution(
        kind="exec",
        decision="allow-once",
    )

    _resolve(service, resolution=resolution, idempotency_key="idem-1")

    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.payload.resolution == resolution.to_dict()
    assert record.payload.idempotency_key == "idem-1"


def test_provider_resolve_same_key_and_resolution_is_idempotent() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    _record_requested(repo, service)
    resolution = InteractionResolution(kind="exec", decision="allow-once")

    first = _resolve(service, resolution=resolution, idempotency_key="idem-1")
    second = _resolve(service, resolution=resolution, idempotency_key="idem-1")

    assert first.interaction_id == BAAS_INTERACTION_ID
    assert second.interaction_id == BAAS_INTERACTION_ID
    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "queued"


@pytest.mark.parametrize("terminal_state", ["failed", "expired"])
def test_provider_resolve_same_key_remains_idempotent_after_terminal_state(
    terminal_state: str,
) -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    _record_requested(repo, service)
    resolution = InteractionResolution(kind="exec", decision="allow-once")
    _resolve(service, resolution=resolution, idempotency_key="idem-1")
    record = repo.rows[("s-1", "int-1")]
    repo.rows[("s-1", "int-1")] = replace(record, state=terminal_state)

    result = _resolve(
        service,
        resolution=resolution,
        idempotency_key="idem-1",
    )

    assert result.interaction_id == BAAS_INTERACTION_ID


def test_provider_resolve_rereads_after_lost_transition_race() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    _record_requested(repo, service)
    resolution = InteractionResolution(kind="exec", decision="allow-once")
    original_transition = repo.transition_by_baas_interaction_id

    def transition_as_concurrent_winner(**kwargs):
        updated = original_transition(**kwargs)
        if kwargs["to_state"] == "queued":
            return None
        return updated

    repo.transition_by_baas_interaction_id = (  # type: ignore[method-assign]
        transition_as_concurrent_winner
    )

    result = _resolve(
        service,
        resolution=resolution,
        idempotency_key="idem-1",
    )

    assert result.interaction_id == BAAS_INTERACTION_ID


def test_provider_resolve_same_key_with_different_resolution_conflicts() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    _record_requested(repo, service)
    _resolve(
        service,
        resolution=InteractionResolution(kind="exec", decision="allow-once"),
        idempotency_key="idem-1",
    )

    with pytest.raises(InteractionConflictError):
        _resolve(
            service,
            resolution=InteractionResolution(kind="exec", decision="deny"),
            idempotency_key="idem-1",
        )


def test_provider_resolve_different_key_after_queue_conflicts() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    _record_requested(repo, service)
    resolution = InteractionResolution(kind="exec", decision="allow-once")
    _resolve(service, resolution=resolution, idempotency_key="idem-1")

    with pytest.raises(InteractionConflictError):
        _resolve(service, resolution=resolution, idempotency_key="idem-2")


def test_http_resolve_rejects_unknown_decision() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    _record_requested(repo, service)

    with pytest.raises(InteractionBadRequestError):
        _resolve(service, decision="always")

    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "requested"


def test_http_resolve_rejects_decision_when_allowed_set_is_empty() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    service.record_requested(
        session_key="s-1",
        interaction_id="int-1",
        envelope=_requested_envelope(),
        allowed_decisions=(),
        expires_at_ms=int(time.time() * 1000) + 60_000,
    )

    with pytest.raises(InteractionBadRequestError):
        _resolve(service, decision="hidden")

    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "requested"


def test_legacy_payload_without_allowed_decisions_remains_unrestricted() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    payload = BotRunInteractionPayload.from_dict({"requested": _requested_envelope()})
    assert payload.allowed_decisions is None
    assert "allowedDecisions" not in payload.to_dict()
    repo.rows[("s-1", "int-1")] = BotRunInteractionRecord(
        id=1,
        baas_interaction_id=BAAS_INTERACTION_ID,
        session_key="s-1",
        interaction_id="int-1",
        state="requested",
        payload=payload,
    )

    result = _resolve(service, decision="legacy-decision")

    assert result.interaction_id == BAAS_INTERACTION_ID
    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "queued"
    assert record.payload.decision == "legacy-decision"


def test_explicit_empty_allowed_decisions_round_trip_and_reject_all() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    payload = BotRunInteractionPayload.from_dict(
        {
            "requested": _requested_envelope(),
            "allowedDecisions": [],
        }
    )
    assert payload.allowed_decisions == ()
    assert payload.to_dict()["allowedDecisions"] == []
    repo.rows[("s-1", "int-1")] = BotRunInteractionRecord(
        id=1,
        baas_interaction_id=BAAS_INTERACTION_ID,
        session_key="s-1",
        interaction_id="int-1",
        state="requested",
        payload=BotRunInteractionPayload.from_dict(payload.to_dict()),
    )

    with pytest.raises(InteractionBadRequestError):
        _resolve(service, decision="anything")

    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "requested"
    assert record.payload.allowed_decisions == ()


@pytest.mark.parametrize("decision", ["submit", "cancel"])
def test_ask_user_fixed_actions_can_be_queued(decision: str) -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    event = EngineInteractionRequestedEvent.from_payload(
        session_key="s-1",
        payload={
            "interactionId": "int-1",
            "kind": "ask_user",
            "questions": [{"header": "Name", "question": "Your name?"}],
            "options": [{"label": "Hidden", "decision": "hidden"}],
        },
    )
    service.record_requested(
        session_key=event.session_key,
        interaction_id=event.interaction_id,
        envelope=event.envelope,
        allowed_decisions=event.allowed_decisions,
        expires_at_ms=event.expires_at_ms,
    )

    result = _resolve(service, decision=decision)

    assert result.interaction_id == BAAS_INTERACTION_ID
    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "queued"
    assert record.payload.allowed_decisions == ("submit", "cancel")


def test_ask_user_hidden_top_level_decision_is_rejected() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    event = EngineInteractionRequestedEvent.from_payload(
        session_key="s-1",
        payload={
            "interactionId": "int-1",
            "kind": "ask_user",
            "questions": [{"header": "Name", "question": "Your name?"}],
            "options": [{"label": "Hidden", "decision": "hidden"}],
        },
    )
    service.record_requested(
        session_key=event.session_key,
        interaction_id=event.interaction_id,
        envelope=event.envelope,
        allowed_decisions=event.allowed_decisions,
        expires_at_ms=event.expires_at_ms,
    )

    with pytest.raises(InteractionBadRequestError):
        _resolve(service, decision="hidden")

    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "requested"


def test_parsed_hidden_decision_is_rejected_but_visible_decision_resolves() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    payload = {
        "interactionId": "int-1",
        "kind": "exec",
        "command": "make test",
        "options": [
            {"decision": "hidden"},
            {"label": "Allow once", "decision": "allow-once"},
        ],
    }
    event = EngineInteractionRequestedEvent.from_payload(
        session_key="s-1",
        payload=payload,
    )
    service.record_requested(
        session_key=event.session_key,
        interaction_id=event.interaction_id,
        envelope=event.envelope,
        allowed_decisions=event.allowed_decisions,
        expires_at_ms=event.expires_at_ms,
    )

    with pytest.raises(InteractionBadRequestError):
        _resolve(service, decision="hidden")

    result = _resolve(service, decision="allow-once")
    assert result.interaction_id == BAAS_INTERACTION_ID
    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "queued"
    assert record.payload.allowed_decisions == ("allow-once",)


def test_claim_returns_typed_dispatch_without_exposing_payload_layout() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    _record_requested(repo, service)
    _resolve(service)

    command = service.claim_for_dispatch(session_key="s-1", interaction_id="int-1")

    assert command == InteractionDispatch(
        session_key="s-1",
        interaction_id="int-1",
        resolution=InteractionResolution(decision="allow-once"),
    )
    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "dispatching"


def test_claim_legacy_queued_payload_builds_decision_only_resolution() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    repo.rows[("s-1", "int-1")] = BotRunInteractionRecord(
        id=1,
        baas_interaction_id=BAAS_INTERACTION_ID,
        session_key="s-1",
        interaction_id="int-1",
        state="queued",
        payload=BotRunInteractionPayload(
            requested=_requested_envelope(),
            decision="deny",
        ),
    )

    command = service.claim_for_dispatch(session_key="s-1", interaction_id="int-1")

    assert command == InteractionDispatch(
        session_key="s-1",
        interaction_id="int-1",
        resolution=InteractionResolution(decision="deny"),
    )


def test_duplicate_resolved_transition_is_suppressed() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    _record_requested(repo, service)
    envelope = {
        "type": "event",
        "event": "interaction.resolved",
        "payload": {"interactionId": "different-id"},
    }

    first = service.mark_resolved(
        session_key="s-1", interaction_id="int-1", envelope=envelope
    )
    second = service.mark_resolved(
        session_key="s-1", interaction_id="int-1", envelope=envelope
    )

    assert first is not None
    assert first.baas_interaction_id == BAAS_INTERACTION_ID
    assert first.applied is True
    assert second is not None
    assert second.baas_interaction_id == BAAS_INTERACTION_ID
    assert second.applied is False
    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.payload.resolved == envelope


def test_late_engine_exchange_does_not_modify_terminal_payload() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    _record_requested(repo, service)
    _resolve(service)
    assert (
        service.claim_for_dispatch(session_key="s-1", interaction_id="int-1")
        is not None
    )
    assert service.mark_resolved(
        session_key="s-1",
        interaction_id="int-1",
        envelope={"type": "event", "event": "interaction.resolved", "payload": {}},
    )

    updated = service.record_engine_exchange(
        session_key="s-1",
        interaction_id="int-1",
        engine_req={"type": "req"},
        engine_res={"type": "res", "ok": True},
    )

    assert updated is False
    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.payload.engine_req is None
    assert record.payload.engine_res is None


def test_expiry_can_close_a_queued_answer_before_dispatch() -> None:
    repo = FakeRepo()
    service = DefaultBotInteractionService(repo)
    _record_requested(repo, service)
    _resolve(service)

    assert service.mark_expired(session_key="s-1", interaction_id="int-1") is True
    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "expired"
    assert record.payload.expire_reason == "interaction deadline elapsed"
    assert service.claim_for_dispatch(session_key="s-1", interaction_id="int-1") is None
