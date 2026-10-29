"""Current knowledge state is bounded by live IDs, never accumulated history."""

import copy
import json

import pytest
from sqlalchemy import event

from agentclaw.community.core.bot_knowledge.repository.store import (
    BotKnowledgeRepository,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.kernel.knowledge import BotKnowledgeScope
from agentclaw.community.plugin_api.models import BotCommonConfig
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding,
    _start_online,
    publication as publication_fixture,
)
from tests.community.repository.publishing.test_knowledge_observed_completion import (
    space,
)
from tests.community.repository.publishing.test_knowledge_short_transaction import (
    finish,
)

publication = publication_fixture


def selected(db):
    return BotKnowledgeRepository(db).list_enabled(
        BotKnowledgeScope("service", "owner", "dev"),
        selection_stage="online",
    )


def test_completion_stores_one_current_summary_in_existing_config(publication):
    db, repo, record = publication
    space(db)
    finish(repo, _start_online(repo, record))
    with db.orm_session() as session:
        rows = session.query(BotCommonConfig).all()
        assert len(rows) == 1
        value = json.loads(rows[0].config_value)
        assert value["enabled_kb_ids"] == ["B"]
        assert value["deployment_id"] == 77
        assert value["source_bot_pk"] == 1
        assert value["avernet_tenant"] == "teamclaw"
    assert selected(db) == ("B",)


def test_legacy_flags_without_summary_are_not_effective(publication):
    db, _, _ = publication
    space(db)
    with db.orm_session() as session:
        session.query(Binding).filter_by(kb_id="A").update({"online_enabled": True})
    assert selected(db) == ()


def test_first_trusted_completion_does_not_inherit_untracked_legacy_flags(publication):
    db, repo, record = publication
    space(db)
    with db.orm_session() as session:
        session.query(Binding).filter_by(kb_id="A").update({"online_enabled": True})
    finish(repo, _start_online(repo, record))
    assert selected(db) == ("B",)


def test_corrupt_current_summary_fails_closed_without_history_fallback(publication):
    db, repo, record = publication
    space(db)
    finish(repo, _start_online(repo, record))
    with db.orm_session() as session:
        row = session.query(BotCommonConfig).one()
        row.config_value = "not JSON"
    assert selected(db) == ()


def test_summary_write_failure_rolls_back_native_success_and_flags(publication):
    db, repo, record = publication
    space(db)
    online = _start_online(repo, record)
    engine = db.factory.kw["bind"]

    def fail(_conn, _cursor, statement, *_args):
        if statement.startswith("INSERT INTO ac_bot_common_config"):
            raise RuntimeError("summary persistence failed")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="summary persistence failed"):
            finish(repo, online)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert repo.get_by_id(record.id).status == PublishStatus.ONLINE_PUB
    assert selected(db) == ()
    finish(repo, online)
    assert selected(db) == ("B",)


def test_reader_cannot_mix_summary_and_flags_from_different_completions(publication):
    db, repo, record = publication
    space(db)
    finish(repo, _start_online(repo, record))
    engine = db.factory.kw["bind"]
    raced = []

    def replace_before_binding_query(_conn, _cursor, statement, *_args):
        if (
            not raced
            and statement.startswith("SELECT")
            and "FROM ac_bot_knowledge_binding" in statement
        ):
            raced.append(True)
            with db.orm_session() as session:
                row = session.query(BotCommonConfig).one()
                value = json.loads(row.config_value)
                value.update(status="unknown", deployment_id=200, enabled_kb_ids=[])
                row.config_value = json.dumps(value)

    event.listen(engine, "before_cursor_execute", replace_before_binding_query)
    try:
        assert selected(db) == ()
    finally:
        event.remove(engine, "before_cursor_execute", replace_before_binding_query)
    assert raced


def test_read_and_completion_sql_never_scan_publication_history_or_all_bindings(
    publication,
):
    db, repo, record = publication
    space(db)
    first = finish(repo, _start_online(repo, record))
    with db.orm_session() as session:
        session.query(Binding).update({"enabled": False})
        session.query(Binding).filter_by(kb_id="A").update({"enabled": True})
    following = repo.insert(
        {
            "source_bot_pk": 1,
            "source_bot_id": "service",
            "publish_bot_id": "service.pub",
            "owner_id": "owner",
            "permission_owner": "owner",
            "name": "Service",
            "version": 2,
            "env": "dev",
            "ext": {},
        }
    )
    online = _start_online(repo, following)
    engine = db.factory.kw["bind"]
    statements = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert selected(db) == ("B",)
        repo.compare_and_set_status_with_ext(
            publish_id=following.id,
            source_status=PublishStatus.ONLINE_PUB,
            target_status=PublishStatus.SUCCESS,
            expected_ext=online.ext,
            ext=copy.deepcopy(online.ext),
            observed_workflow_id=200,
        )
        assert selected(db) == ("A",)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    for sql in statements:
        if "FROM ac_bot_publish" in sql:
            assert "WHERE ac_bot_publish.id =" in sql, sql
            assert "JSON_" not in sql, sql
        if sql.startswith("UPDATE ac_bot_knowledge_binding"):
            assert (
                "WHERE ac_bot_knowledge_binding.id =" in sql and "avernet_tenant" in sql
            ), sql
    assert first.ext["knowledge_selection_result"]["status"] == "applied"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_bot_pk", 999),
        ("source_entity_id", "foreign"),
        ("avernet_tenant", "foreign"),
        ("owner_id", "foreign"),
        ("space_id", 999),
        (
            "materialized",
            {"avernet_tenant": "teamclaw", "space_id": 9, "kb_ids": ["A"]},
        ),
        ("enabled_kb_ids", ["B", "B"]),
    ],
)
def test_summary_scope_or_materialization_corruption_cannot_enable_flags(
    publication, field, value
):
    db, repo, record = publication
    space(db)
    finish(repo, _start_online(repo, record))
    with db.orm_session() as session:
        row = session.query(BotCommonConfig).one()
        summary = json.loads(row.config_value)
        summary[field] = value
        row.config_value = json.dumps(summary)
        session.query(Binding).update({"online_enabled": True})
    assert selected(db) == ()


def test_summary_updates_roll_back_on_commit_failure(publication):
    from contextlib import contextmanager
    from tests.community.core.bot_knowledge.summary_fixtures import seed_online

    db, repo, record = publication
    space(db)
    seed_online(db, record, ["A"])
    with db.orm_session() as session:
        session.query(Binding).filter_by(kb_id="A").update({"online_enabled": True})
    online = _start_online(repo, record)
    original = db.transactional_orm_session

    @contextmanager
    def fail_commit():
        with original() as session:
            yield session
            raise RuntimeError("commit refused")

    db.transactional_orm_session = fail_commit
    try:
        with pytest.raises(RuntimeError, match="commit refused"):
            finish(repo, online)
    finally:
        db.transactional_orm_session = original
    assert repo.get_by_id(record.id).status == PublishStatus.ONLINE_PUB
    assert selected(db) == ("A",)
    finish(repo, online)
    assert selected(db) == ("B",)


def test_space_change_clears_only_tracked_old_keys_and_installs_new_selection(
    publication,
):
    from tests.community.core.bot_knowledge.summary_fixtures import seed_online
    from agentclaw.community.plugin_api.models import BotModel

    db, repo, record = publication
    space(db)
    seed_online(db, record, ["A"])
    with db.orm_session() as session:
        session.query(Binding).filter_by(kb_id="A").update({"online_enabled": True})
        session.query(BotModel).filter_by(id=1).update({"space_id": 10})
        session.query(Binding).filter_by(kb_id="B").update({"space_id": 10})
    completed = finish(repo, _start_online(repo, record))
    assert completed.ext["knowledge_selection_result"]["status"] == "applied"
    with db.orm_session() as session:
        assert dict(session.query(Binding.kb_id, Binding.online_enabled).all()) == {
            "A": False,
            "B": True,
        }
        summary = json.loads(session.query(BotCommonConfig).one().config_value)
        assert summary["space_id"] == 10 and summary["enabled_kb_ids"] == ["B"]
        assert "kb_ids" not in summary["materialized"]


@pytest.mark.parametrize("dialect_name", ["sqlite", "mysql", "postgresql"])
def test_current_summary_query_uses_text_fence_not_json_operators(dialect_name):
    from sqlalchemy import select
    from sqlalchemy.dialects import mysql, postgresql, sqlite
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from agentclaw.community.core.bot_knowledge.repository.online_selection import (
        OnlineSelection,
        config_scope,
    )

    dialect = {"sqlite": sqlite, "mysql": mysql, "postgresql": postgresql}[
        dialect_name
    ].dialect()
    selection = OnlineSelection(
        config_scope(1, "service", "owner", "dev"), '{"owner_id":"Owner"}', {}, ("B",)
    )
    session = MagicMock()
    session.get_bind.return_value = SimpleNamespace(dialect=dialect)
    sql = str(select(selection.unchanged(session)).compile(dialect=dialect))
    assert "ac_bot_common_config" in sql
    assert (
        "ac_bot_publish" not in sql and "JSON_EXTRACT" not in sql and " -> " not in sql
    )
    if dialect_name == "mysql":
        assert " AS BINARY" in sql  # don't inherit a case-insensitive TEXT collation


@pytest.mark.parametrize("character", ["知", "😀"])
def test_maximum_multibyte_selection_fits_existing_text_and_round_trips(
    publication, character
):
    from agentclaw.community.core.bot_knowledge.repository.online_selection import (
        decode_summary,
    )

    db, repo, record = publication
    space(db)
    ids = [character * 125 + f"{i:03d}" for i in range(100)]
    with db.orm_session() as session:
        session.query(Binding).delete()
        session.add_all(
            [
                Binding(
                    bot_pk=1,
                    space_id=9,
                    env="dev",
                    kb_id=kb,
                    enabled=True,
                    online_enabled=False,
                    revision=1,
                    updated_by="owner",
                )
                for kb in ids
            ]
        )
    completed = finish(repo, _start_online(repo, record))
    assert completed.ext["knowledge_selection_result"]["status"] == "applied"
    with db.orm_session() as session:
        raw = session.query(BotCommonConfig).one().config_value
    assert len(raw.encode("utf-8")) <= 65535
    decoded = decode_summary(
        raw, source_pk=1, bot_id="service", entity_id="owner", env="dev"
    )
    assert decoded is not None
    assert (
        set(decoded["enabled_kb_ids"])
        == set(decoded["materialized"]["kb_ids"])
        == set(ids)
    )
    assert "kb_ids" not in json.loads(raw)["materialized"]
    assert set(selected(db)) == set(ids)
