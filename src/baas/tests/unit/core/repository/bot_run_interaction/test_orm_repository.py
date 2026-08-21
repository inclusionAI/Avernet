"""Real SQLite tests for interaction payload/state persistence."""

from __future__ import annotations

import pytest

import secbaas.community.core.repository.bot_run_interaction._orm_model  # noqa: F401
from secbaas.community.core.database import DatabaseManager
from secbaas.community.core.repository.bot_run_interaction import (
    BotRunInteractionPayload,
    BotRunInteractionPayloadPatch,
    OrmBotRunInteractionRepository,
)
from secbaas.community.plugins.database.sqlite.sqlite_orm import SqliteOrmPlugin


@pytest.fixture
def repository() -> OrmBotRunInteractionRepository:
    plugin = SqliteOrmPlugin("sqlite:///:memory:")
    plugin.create_all()
    database = DatabaseManager()
    database._sync_session_factory = plugin._sync_session_factory
    database._sync_engine = plugin._sync_engine
    return OrmBotRunInteractionRepository(database=database)


def test_create_requested_is_idempotent(
    repository: OrmBotRunInteractionRepository,
) -> None:
    payload = BotRunInteractionPayload(
        requested={"type": "event"},
        allowed_decisions=("allow-once", "deny"),
    )

    first = repository.create_requested(
        session_key="s-1", interaction_id="int-1", payload=payload
    )
    second = repository.create_requested(
        session_key="s-1", interaction_id="int-1", payload=payload
    )

    assert first.created is True
    assert second.created is False
    assert second.record.id == first.record.id


def test_transition_merges_typed_payload(
    repository: OrmBotRunInteractionRepository,
) -> None:
    repository.create_requested(
        session_key="s-1",
        interaction_id="int-1",
        payload=BotRunInteractionPayload(requested={"type": "event"}),
    )

    queued = repository.transition(
        session_key="s-1",
        interaction_id="int-1",
        from_states=frozenset({"requested"}),
        to_state="queued",
        patch=BotRunInteractionPayloadPatch(
            decision="allow-once",
            client_req={"type": "req"},
            resolution={"kind": "exec", "decision": "allow-once"},
            idempotency_key="idem-1",
        ),
    )

    assert queued is not None
    assert queued.state == "queued"
    assert queued.payload.requested == {"type": "event"}
    assert queued.payload.decision == "allow-once"
    assert queued.payload.client_req == {"type": "req"}
    assert queued.payload.resolution == {
        "kind": "exec",
        "decision": "allow-once",
    }
    assert queued.payload.idempotency_key == "idem-1"


def test_late_payload_write_is_rejected_after_terminal_transition(
    repository: OrmBotRunInteractionRepository,
) -> None:
    repository.create_requested(
        session_key="s-1",
        interaction_id="int-1",
        payload=BotRunInteractionPayload(requested={"type": "event"}),
    )
    resolved = repository.transition(
        session_key="s-1",
        interaction_id="int-1",
        from_states=frozenset({"requested"}),
        to_state="resolved",
        patch=BotRunInteractionPayloadPatch(resolved={"type": "event"}),
    )
    assert resolved is not None

    late = repository.merge_payload(
        session_key="s-1",
        interaction_id="int-1",
        allowed_states=frozenset({"dispatching"}),
        patch=BotRunInteractionPayloadPatch(engine_res={"ok": True}),
    )

    assert late is None
    stored = repository.get(session_key="s-1", interaction_id="int-1")
    assert stored is not None
    assert stored.payload.resolved == {"type": "event"}
    assert stored.payload.engine_res is None


def test_corrupt_payload_shape_is_not_silently_downgraded(
    repository: OrmBotRunInteractionRepository,
) -> None:
    created = repository.create_requested(
        session_key="s-1",
        interaction_id="int-1",
        payload=BotRunInteractionPayload(requested={"type": "event"}),
    )
    with repository._database.orm_session() as session:
        from secbaas.community.core.repository.bot_run_interaction import (
            BotRunInteractionModel,
        )

        session.query(BotRunInteractionModel).filter(
            BotRunInteractionModel.id == created.record.id
        ).update({"payload": '{"requested":[],"decision":7}'})
        session.commit()

    with pytest.raises(ValueError, match="requested must be an object"):
        repository.get(session_key="s-1", interaction_id="int-1")
