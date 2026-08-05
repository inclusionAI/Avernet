from __future__ import annotations

import time
from dataclasses import replace

import pytest

from secbaas.community.core.repository.bot_run_interaction import (
    BotRunInteractionCreateResult,
    BotRunInteractionPayload,
    BotRunInteractionPayloadPatch,
    BotRunInteractionRecord,
)
from secbaas.community.core.service.bot_interaction import (
    BotInteractionService,
    InteractionBadRequestError,
    InteractionDispatch,
)


class FakeRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], BotRunInteractionRecord] = {}
        self.next_id = 1

    def create_requested(
        self,
        *,
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


def _record_requested(repo: FakeRepo, service: BotInteractionService) -> None:
    created = service.record_requested(
        session_key="s-1",
        interaction_id="int-1",
        envelope=_requested_envelope(),
        allowed_decisions=("allow-once", "deny"),
        expires_at_ms=int(time.time() * 1000) + 60_000,
    )
    assert created is True


def _resolve(service: BotInteractionService, *, decision: str = "allow-once"):
    request = {
        "type": "req",
        "id": "client-1",
        "method": "interaction.resolve",
        "params": {
            "sessionKey": "s-1",
            "interactionId": "int-1",
            "decision": decision,
        },
    }
    return service.resolve(
        session_key="s-1",
        interaction_id="int-1",
        decision=decision,
        request_envelope=request,
    )


def test_record_requested_uses_explicit_identity_not_envelope_identity() -> None:
    repo = FakeRepo()
    service = BotInteractionService(repo)

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


def test_duplicate_requested_is_idempotent() -> None:
    repo = FakeRepo()
    service = BotInteractionService(repo)
    kwargs = {
        "session_key": "s-1",
        "interaction_id": "int-1",
        "envelope": _requested_envelope(),
        "allowed_decisions": ("allow-once", "deny"),
        "expires_at_ms": None,
    }

    assert service.record_requested(**kwargs) is True
    assert service.record_requested(**kwargs) is False
    assert len(repo.rows) == 1


def test_http_resolve_moves_requested_to_queued() -> None:
    repo = FakeRepo()
    service = BotInteractionService(repo)
    _record_requested(repo, service)

    result = _resolve(service)

    assert result.interaction_id == "int-1"
    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "queued"
    assert record.payload.decision == "allow-once"
    assert record.payload.client_req is not None
    assert record.payload.client_req["id"] == "client-1"


def test_http_resolve_rejects_unknown_decision() -> None:
    repo = FakeRepo()
    service = BotInteractionService(repo)
    _record_requested(repo, service)

    with pytest.raises(InteractionBadRequestError):
        _resolve(service, decision="always")

    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "requested"


def test_claim_returns_typed_dispatch_without_exposing_payload_layout() -> None:
    repo = FakeRepo()
    service = BotInteractionService(repo)
    _record_requested(repo, service)
    _resolve(service)

    command = service.claim_for_dispatch(session_key="s-1", interaction_id="int-1")

    assert command == InteractionDispatch(
        session_key="s-1",
        interaction_id="int-1",
        decision="allow-once",
    )
    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "dispatching"


def test_duplicate_resolved_transition_is_suppressed() -> None:
    repo = FakeRepo()
    service = BotInteractionService(repo)
    _record_requested(repo, service)
    envelope = {
        "type": "event",
        "event": "interaction.resolve",
        "payload": {"interactionId": "different-id"},
    }

    assert (
        service.mark_resolved(
            session_key="s-1", interaction_id="int-1", envelope=envelope
        )
        is True
    )
    assert (
        service.mark_resolved(
            session_key="s-1", interaction_id="int-1", envelope=envelope
        )
        is False
    )
    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.payload.resolved == envelope


def test_late_engine_exchange_does_not_modify_terminal_payload() -> None:
    repo = FakeRepo()
    service = BotInteractionService(repo)
    _record_requested(repo, service)
    _resolve(service)
    assert (
        service.claim_for_dispatch(session_key="s-1", interaction_id="int-1")
        is not None
    )
    assert service.mark_resolved(
        session_key="s-1",
        interaction_id="int-1",
        envelope={"type": "event", "event": "interaction.resolve", "payload": {}},
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
    service = BotInteractionService(repo)
    _record_requested(repo, service)
    _resolve(service)

    assert service.mark_expired(session_key="s-1", interaction_id="int-1") is True
    record = repo.get(session_key="s-1", interaction_id="int-1")
    assert record is not None
    assert record.state == "expired"
    assert record.payload.expire_reason == "interaction deadline elapsed"
    assert service.claim_for_dispatch(session_key="s-1", interaction_id="int-1") is None
