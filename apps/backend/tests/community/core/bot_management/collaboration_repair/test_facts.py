"""Read-only collaboration repair facts over real SQL rows.

同一战线口径：镜像 ``tests/community/repository/bot/test_ownership_sync_reader.py``
—— 通过 ``world.get(DatabasePlugin)`` 获得真实 SQLite 会话，用既有 ``seed_sync_facts``
播种，再直接更新头像/caller 字段后断言 facts 读取器看到的是当前已保存记录。
读取器任何路径都不得触发业务 mutator，也不得 bump
``caller_config_revision``。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import ORMExecuteState, Session

from agentclaw.community.core.repository.implementations.bot.collaboration_repair import (
    SqlCollaborationRepairReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.kernel.collaboration_repair import (
    ProfileSnapshot,
    RepairFacts,
    RepairFactsError,
)
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


def _update_bot(db: DatabasePlugin, bot_pk: int, **columns: object) -> None:
    """Update one seeded Bot row's persisted columns under its own tenant."""
    with avernet_tenant_scope("t"), db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == bot_pk).update(columns)


def _reader(db: DatabasePlugin) -> SqlCollaborationRepairReadRepository:
    return SqlCollaborationRepairReadRepository(db)


def test_read_facts_projects_saved_profile_and_owner(db: DatabasePlugin) -> None:
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=7, entity_id="stable", owner_id="B"
    )
    _update_bot(
        db,
        7,
        bot_name="Bot",
        bot_desc="Summary",
        ext=json.dumps({"avatar_url": "https://example.com/a.png"}),
        call_type="caller",
        caller_config_revision=3,
    )

    facts = _reader(db).read_facts(key)

    assert facts == RepairFacts(
        key=key,
        profile=ProfileSnapshot(
            bot_id="bot-stable",
            bcs_bot_id="bot-stable:stable",
            name="Bot",
            summary="Summary",
            avatar_url="https://example.com/a.png",
        ),
        owner_user_id="B",
    )


def test_facts_re_read_after_avatar_and_owner_update(db: DatabasePlugin) -> None:
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=8, entity_id="stable", owner_id="B"
    )
    _update_bot(db, 8, ext=json.dumps({"avatar_url": "https://example.com/first.png"}))
    before = _reader(db).read_facts(key)
    assert before.profile.avatar_url == "https://example.com/first.png"
    assert before.profile.name == "bot-stable"
    assert before.profile.summary == ""

    _update_bot(
        db,
        8,
        owner_id="new-owner",
        bot_name="Renamed",
        bot_desc="New summary",
        ext=json.dumps({"avatar_url": "https://example.com/second.png"}),
    )

    after = _reader(db).read_facts(key)
    assert after.owner_user_id == "new-owner"
    assert after.profile.name == "Renamed"
    assert after.profile.summary == "New summary"
    assert after.profile.avatar_url == "https://example.com/second.png"


def test_explicit_avatar_clear_flows_into_profile(db: DatabasePlugin) -> None:
    """``avatar_from_record`` 的 ``""``（显式无头像）原样进入 ProfileSnapshot。"""
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=9, entity_id="stable", owner_id="B"
    )
    _update_bot(db, 9, ext=json.dumps({"avatar_url": "https://example.com/a.png"}))
    assert _reader(db).read_facts(key).profile.avatar_url == "https://example.com/a.png"

    _update_bot(db, 9, ext=json.dumps({"avatar_url": ""}))
    assert _reader(db).read_facts(key).profile.avatar_url == ""


def test_owner_change_never_leaks_into_the_stable_address(db: DatabasePlugin) -> None:
    """owner≠entity：稳定地址由 entity 组成，绝不用当前 Owner 代替。"""
    key = seed_sync_facts(
        db,
        tenant="t",
        env="dev",
        bot_pk=11,
        entity_id="staff_orig",
        owner_id="orig-owner",
    )
    _update_bot(db, 11, owner_id="handoff-owner")

    facts = _reader(db).read_facts(key)

    assert facts.profile.bcs_bot_id == "bot-staff_orig:staff_orig"
    assert facts.owner_user_id == "handoff-owner"
    assert facts.profile.bot_id == "bot-staff_orig"
    assert "handoff-owner" not in facts.profile.bcs_bot_id
    assert "orig-owner" not in facts.profile.bcs_bot_id


@pytest.mark.parametrize(
    "bot_type,bot_id,reference,expected",
    [
        ("personal", "b", None, "b:owner"),
        ("desktop", "b", None, "b:owner"),
        ("service", "default", None, "default:owner"),
        ("service", "b", None, "b:staff_original"),
        ("service", "b", "b:original-owner", "b:original-owner"),
    ],
)
def test_profile_uses_the_registered_reference(
    db: DatabasePlugin, bot_type: str, bot_id: str,
    reference: str | None, expected: str,
) -> None:
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=21,
        entity_id="staff_original", owner_id="owner", bot_id=bot_id,
    )
    _update_bot(
        db, 21, bot_type=bot_type,
        ext=json.dumps({"bcn_provider_bot_ref": reference}),
        call_type="caller", caller_config_revision=3,
    )

    facts = _reader(db).read_facts(key)

    assert facts.profile.bcs_bot_id == expected


@pytest.mark.parametrize("reference", ["", "other:owner", "b:", 123])
def test_invalid_retained_reference_does_not_fall_back_to_entity(
    db: DatabasePlugin, reference: object,
) -> None:
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=22,
        entity_id="staff_original", owner_id="owner", bot_id="b",
    )
    _update_bot(db, 22, ext=json.dumps({"bcn_provider_bot_ref": reference}))

    with pytest.raises(RepairFactsError, match="collaboration_bot_identity_unresolved"):
        _reader(db).read_facts(key)


def test_missing_deleted_and_wrong_scope_never_produce_facts(
    db: DatabasePlugin,
) -> None:
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=12, entity_id="stable", owner_id="B"
    )
    reader = _reader(db)
    for missing in (
        SyncKey("other", "dev", 12),
        SyncKey("t", "pre", 12),
        SyncKey("t", "dev", 999),
    ):
        with pytest.raises(RepairFactsError) as excinfo:
            reader.read_facts(missing)
        assert excinfo.value.code == "collaboration_bot_not_found"

    _update_bot(db, 12, is_delete=1)
    with pytest.raises(RepairFactsError) as excinfo:
        reader.read_facts(key)
    assert excinfo.value.code == "collaboration_bot_not_found"


def test_reader_binds_the_keys_tenant_not_the_ambient_one(db: DatabasePlugin) -> None:
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=13, entity_id="stable", owner_id="B"
    )
    with avernet_tenant_scope("caller"):
        facts = _reader(db).read_facts(key)
        assert get_current_avernet_tenant() == "caller"
    assert facts.owner_user_id == "B"


@pytest.mark.parametrize("phase", ["open", "query", "close"])
def test_sql_failure_is_a_facts_error_not_empty_facts(
    db: DatabasePlugin,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=14, entity_id="stable", owner_id="B"
    )
    original_session = db.orm_session
    failure = SQLAlchemyError("database unavailable")

    def fail_query(state: ORMExecuteState) -> None:
        raise failure

    @contextmanager
    def failing_session() -> Iterator[Session]:
        if phase == "open":
            raise failure
        with original_session() as session:
            if phase == "query":
                event.listen(session, "do_orm_execute", fail_query)
            yield session
            if phase == "close":
                raise failure

    monkeypatch.setattr(db, "orm_session", failing_session)
    with pytest.raises(RepairFactsError) as excinfo:
        _reader(db).read_facts(key)
    assert excinfo.value.code == "collaboration_facts_read_error"
    assert excinfo.value.__cause__ is failure


@pytest.mark.parametrize(
    "ext_value",
    [
        "{",  # 损坏的 ext JSON
        json.dumps({"avatar_url": 123}),  # avatar 字段类型非法
    ],
)
def test_unreadable_avatar_is_a_facts_error(db: DatabasePlugin, ext_value: str) -> None:
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=15, entity_id="stable", owner_id="B"
    )
    _update_bot(db, 15, ext=ext_value)
    with pytest.raises(RepairFactsError) as excinfo:
        _reader(db).read_facts(key)
    assert excinfo.value.code == "collaboration_avatar_unreadable"


@pytest.mark.parametrize(
    "entity_id,bot_id",
    [
        ("", "bot-blank-entity"),
        (" ", "bot-blank-entity"),
        ("stable", ""),
        ("stable", " padded"),
    ],
)
def test_unresolvable_identity_is_a_facts_error(
    db: DatabasePlugin, entity_id: str, bot_id: str
) -> None:
    key = seed_sync_facts(
        db,
        tenant="t",
        env="dev",
        bot_pk=16,
        entity_id=entity_id,
        owner_id="B",
        bot_id=bot_id,
    )
    with pytest.raises(RepairFactsError) as excinfo:
        _reader(db).read_facts(key)
    assert excinfo.value.code == "collaboration_bot_identity_unresolved"


def test_reads_are_select_only_and_never_bump_caller_revision(
    db: DatabasePlugin,
) -> None:
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=20, entity_id="stable", owner_id="B"
    )
    _update_bot(db, 20, call_type="caller", caller_config_revision=6)
    reader = _reader(db)
    statements: list[ORMExecuteState] = []

    def record(state: ORMExecuteState) -> None:
        statements.append(state)

    event.listen(Session, "do_orm_execute", record)
    try:
        reader.read_facts(key)
        reader.read_facts(key)
    finally:
        event.remove(Session, "do_orm_execute", record)

    assert len(statements) == 2
    assert all(state.is_select for state in statements)
    assert all(state.all_mappers == [inspect(BotModel)] for state in statements)

    with avernet_tenant_scope("t"), db.orm_session() as session:
        saved = session.get(BotModel, 20)
        assert saved is not None
        assert saved.caller_config_revision == 6
        assert saved.call_type == "caller"


@pytest.mark.parametrize("call_type,revision", [("owner", 0), ("caller", 4), ("bogus", 1)])
def test_repair_facts_ignore_caller_configuration(db, call_type, revision):
    key = seed_sync_facts(
        db, tenant="t", env="dev", bot_pk=21, entity_id="stable", owner_id="B"
    )
    _update_bot(db, 21, call_type=call_type, caller_config_revision=revision)

    facts = _reader(db).read_facts(key)

    assert facts.profile.bcs_bot_id == "bot-stable:stable"
    assert not hasattr(facts, "caller")
