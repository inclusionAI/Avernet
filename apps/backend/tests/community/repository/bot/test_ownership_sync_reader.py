"""Current Owner facts are independent of manager rosters and Space state."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import ORMExecuteState, Session

from agentclaw.community.core.repository.implementations.bot.ownership_sync import (
    SqlOwnershipSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import SnapshotUnavailable, SyncKey
from agentclaw.community.kernel.bot_ownership_sync import OwnershipFacts
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)
from tests.community.core.bot_collaborator.manager_sync.factories import (
    seed_sync_facts,
)
from tests.community.framework.fixtures import (
    app_with_testing_modules as app_with_testing_modules,
    world as world,
)
from tests.community.framework.world import World


@pytest.fixture
def db(world: World) -> DatabasePlugin:
    return world.get(DatabasePlugin)


def test_read_owner_ignores_missing_space_and_large_roster(world: World) -> None:
    db = world.get(DatabasePlugin)
    key = seed_sync_facts(
        db,
        tenant="t",
        env="dev",
        bot_pk=7,
        entity_id="stable",
        owner_id="B",
        space_id=99999,
        rows=tuple((f"u{i}", "member") for i in range(1001)),
    )
    reader = SqlOwnershipSyncReadRepository(db)

    assert reader.read_facts(key) == OwnershipFacts(key, "bot-stable", "stable", "B", bot_type="service")
    assert reader.page_keys(avernet_tenant="t", env="dev", after_pk=0, limit=100) == (
        key,
    )


def test_read_facts_rejects_wrong_scope_deleted_and_missing(db: DatabasePlugin) -> None:
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=7, entity_id="stable", owner_id="B"
    )
    reader = SqlOwnershipSyncReadRepository(db)
    missing_keys = (
        SyncKey("other", "dev", 7),
        SyncKey("t", "pre", 7),
        SyncKey("t", "dev", 999),
    )
    with avernet_tenant_scope("caller"):
        for missing in missing_keys:
            with pytest.raises(SnapshotUnavailable) as excinfo:
                reader.read_facts(missing)
            assert excinfo.value.code == "bot_not_found"
            assert get_current_avernet_tenant() == "caller"
        assert reader.read_facts(key).owner_id == "B"
        assert get_current_avernet_tenant() == "caller"

    with avernet_tenant_scope("t"), db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 7).update({"is_delete": 1})
    with pytest.raises(SnapshotUnavailable) as excinfo:
        reader.read_facts(key)
    assert excinfo.value.code == "bot_not_found"


def test_read_facts_is_fresh_after_another_session_commits(db: DatabasePlugin) -> None:
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=7, entity_id="stable", owner_id="A"
    )
    reader = SqlOwnershipSyncReadRepository(db)
    before = reader.read_facts(key)
    assert before == OwnershipFacts(key, "bot-stable", "stable", "A", bot_type="service")

    with avernet_tenant_scope("t"), db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 7).update({"owner_id": "B"})

    assert reader.read_facts(key) == OwnershipFacts(key, "bot-stable", "stable", "B", bot_type="service")
    assert before.owner_id == "A"


def test_reads_only_bot_facts_without_collaborators(db: DatabasePlugin) -> None:
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=7, entity_id="stable", owner_id="B"
    )
    reader = SqlOwnershipSyncReadRepository(db)
    statements: list[ORMExecuteState] = []

    def record(state: ORMExecuteState) -> None:
        statements.append(state)

    event.listen(Session, "do_orm_execute", record)
    try:
        with avernet_tenant_scope("caller"):
            facts = reader.read_facts(key)
            page = reader.page_keys(
                avernet_tenant="t", env="dev", after_pk=0, limit=100
            )
            assert get_current_avernet_tenant() == "caller"
    finally:
        event.remove(Session, "do_orm_execute", record)

    assert facts == OwnershipFacts(key, "bot-stable", "stable", "B", bot_type="service")
    assert page == (key,)
    assert len(statements) == 2
    assert all(state.is_select for state in statements)
    assert all(state.all_mappers == [inspect(BotModel)] for state in statements)


def test_page_keys_scopes_live_bots_and_uses_exclusive_sorted_cursor(
    db: DatabasePlugin,
) -> None:
    for pk, tenant, env in (
        (9, "t", "dev"),
        (3, "t", "dev"),
        (7, "t", "dev"),
        (4, "other", "dev"),
        (5, "t", "pre"),
        (6, "t", "dev"),
    ):
        seed_sync_facts(
            db,
            tenant=tenant,
            env=env,
            bot_pk=pk,
            entity_id=f"entity-{pk}",
            owner_id="B",
            rows=(),
            space_id=99999,
        )
    with avernet_tenant_scope("t"), db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 6).update({"is_delete": 1})
    reader = SqlOwnershipSyncReadRepository(db)

    assert reader.page_keys(avernet_tenant="t", env="dev", after_pk=0, limit=2) == (
        SyncKey("t", "dev", 3),
        SyncKey("t", "dev", 7),
    )
    assert reader.page_keys(avernet_tenant="t", env="dev", after_pk=7, limit=2) == (
        SyncKey("t", "dev", 9),
    )
    assert reader.page_keys(avernet_tenant="t", env="dev", after_pk=9, limit=2) == ()
    assert reader.page_keys(
        avernet_tenant="other", env="dev", after_pk=0, limit=100
    ) == (SyncKey("other", "dev", 4),)
    assert reader.page_keys(avernet_tenant="t", env="pre", after_pk=0, limit=100) == (
        SyncKey("t", "pre", 5),
    )


@pytest.mark.parametrize("tenant,env", [("t", "dev"), ("absent", "dev"), ("t", "pre")])
def test_page_keys_empty_scope(db: DatabasePlugin, tenant: str, env: str) -> None:
    assert (
        SqlOwnershipSyncReadRepository(db).page_keys(
            avernet_tenant=tenant, env=env, after_pk=0, limit=100
        )
        == ()
    )


@pytest.mark.parametrize("limit", [0, -1])
def test_page_keys_nonpositive_limit_never_opens_a_session(
    db: DatabasePlugin,
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
) -> None:
    seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=7, entity_id="stable", owner_id="B"
    )

    def unexpected_session() -> None:
        pytest.fail("a zero-sized page must not open a session")

    monkeypatch.setattr(db, "orm_session", unexpected_session)
    assert (
        SqlOwnershipSyncReadRepository(db).page_keys(
            avernet_tenant="t", env="dev", after_pk=0, limit=limit
        )
        == ()
    )


def test_page_keys_clamps_101_to_100_without_skipping_remaining_bot(
    db: DatabasePlugin,
) -> None:
    for pk in range(1, 102):
        seed_sync_facts(
            db,
            tenant="t",
            env="dev",
            bot_pk=pk,
            entity_id=f"entity-{pk}",
            owner_id="B",
            rows=(),
        )
    reader = SqlOwnershipSyncReadRepository(db)

    assert reader.page_keys(
        avernet_tenant="t", env="dev", after_pk=0, limit=101
    ) == tuple(SyncKey("t", "dev", pk) for pk in range(1, 101))
    assert reader.page_keys(avernet_tenant="t", env="dev", after_pk=100, limit=101) == (
        SyncKey("t", "dev", 101),
    )


@pytest.mark.parametrize("operation", ["read_facts", "page_keys"])
@pytest.mark.parametrize("phase", ["open", "query", "close"])
def test_sql_errors_map_to_read_error_and_restore_tenant(
    db: DatabasePlugin,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    phase: str,
) -> None:
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=7, entity_id="stable", owner_id="B"
    )
    original_session = db.orm_session
    failure = SQLAlchemyError("database unavailable")

    def fail_query(state: ORMExecuteState) -> None:
        raise failure

    @contextmanager
    def failing_session() -> Iterator[Session]:
        assert get_current_avernet_tenant() == "t"
        if phase == "open":
            raise failure
        with original_session() as session:
            if phase == "query":
                event.listen(session, "do_orm_execute", fail_query)
            yield session
            if phase == "close":
                raise failure

    monkeypatch.setattr(db, "orm_session", failing_session)
    reader = SqlOwnershipSyncReadRepository(db)
    with avernet_tenant_scope("caller"):
        with pytest.raises(SnapshotUnavailable) as excinfo:
            if operation == "read_facts":
                reader.read_facts(key)
            else:
                reader.page_keys(avernet_tenant="t", env="dev", after_pk=0, limit=1)
        assert get_current_avernet_tenant() == "caller"
    assert excinfo.value.code == "read_error"
    assert excinfo.value.__cause__ is failure


@pytest.mark.parametrize("bot_type,expected", [("personal", "bot-stable:B"), ("service", "bot-stable:original")])
def test_readers_preserve_type_and_retained_registration_reference(db, bot_type, expected):
    import json

    from agentclaw.community.core.repository.implementations.bot.manager_sync import SqlManagerSyncReadRepository
    from agentclaw.community.core.bot_management.ownership_sync.projector import build_snapshot as owner_snapshot
    from agentclaw.community.core.bot_collaborator.manager_sync.projector import build_snapshot as manager_snapshot
    key = seed_sync_facts(db, tenant="t", env="dev", bot_pk=7, entity_id="stable", owner_id="B")
    with avernet_tenant_scope("t"), db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 7).update({
            "bot_type": bot_type, "ext": json.dumps({"bcn_provider_bot_ref": "bot-stable:original"}),
        })
    ownership = SqlOwnershipSyncReadRepository(db).read_facts(key)
    managers = SqlManagerSyncReadRepository(db).read_facts(key)
    assert ownership.bot_type == managers.bot_type == bot_type
    assert ownership.bcn_provider_bot_ref == managers.bcn_provider_bot_ref == "bot-stable:original"
    assert owner_snapshot(ownership).bcs_bot_id == manager_snapshot(managers).bcs_bot_id == expected


@pytest.mark.parametrize("raw_ext", ["{", "[]", "null"])
def test_invalid_serialized_metadata_fails_closed(db, raw_ext):
    from agentclaw.community.core.repository.implementations.bot.manager_sync import SqlManagerSyncReadRepository
    key = seed_sync_facts(db, tenant="t", env="dev", bot_pk=7, entity_id="stable", owner_id="B")
    with avernet_tenant_scope("t"), db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 7).update({"ext": raw_ext})
    for reader in (SqlOwnershipSyncReadRepository(db), SqlManagerSyncReadRepository(db)):
        with pytest.raises(SnapshotUnavailable, match="invalid_bot_ext"):
            reader.read_facts(key)
