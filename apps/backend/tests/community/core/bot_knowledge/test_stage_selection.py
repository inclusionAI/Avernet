"""Stage selection isolation uses real persisted rows, not private reader mocks."""

import json

import pytest
from sqlalchemy import event

from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel as Binding,
)
from agentclaw.community.core.bot_knowledge.stage_selection import (
    BotKnowledgeStageSelection,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel as Publication,
)
from agentclaw.community.plugin_api.models import BotCommonConfig, BotModel


def project(observer, *, publication_id):
    observed = observer.observe_online_selection(publication_id=publication_id)
    return observer.project_online_selection(
        publication_id=publication_id, observed=observed
    )


def source(db):
    with db.orm_session() as session:
        return session.get(BotModel, 1).to_dict()


def terminal(db, publication_id=101, workflow_id=301):
    with db.orm_session() as session:
        publication = session.get(Publication, publication_id)
        publication.status = "success"
        publication.ext = json.dumps({"publish": {"online": workflow_id}})


def test_capture_then_verify_then_online_projection_does_not_change_draft_cas(
    observation,
):
    db, observer, _ = observation
    reader = BotKnowledgeStageSelection(db)
    assert observer.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        session.get(Publication, 101).status = "validating"
        row = session.query(Binding).one()
        row.enabled = False  # New draft/edit must never be substituted into verify.
        before = (row.revision, row.updated_by)
    assert reader.read(bot=source(db), runtime_stage="verify")["kb_ids"] == ["kb"]
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] is None
    terminal(db)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] is None
    assert project(observer, publication_id=101)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] == ["kb"]
    with db.orm_session() as session:
        row = session.query(Binding).one()
        assert row.online_enabled is True
        assert row.enabled is False
        assert (row.revision, row.updated_by) == before


def test_rollback_target_snapshot_restores_only_online_and_preserves_history(
    observation,
):
    db, observer, _ = observation
    reader = BotKnowledgeStageSelection(db)
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    assert project(observer, publication_id=101)
    with db.orm_session() as session:
        session.get(Publication, 101).status = "upgraded"
        session.add(
            Publication(
                id=102,
                source_bot_pk=1,
                source_bot_id="bot",
                publish_bot_id="botpub2",
                name="Bot",
                owner_id="owner",
                status="building",
                version=2,
                last_pub_id=101,
                env="dev",
                permission_owner="owner",
            )
        )
        session.query(Binding).update({"enabled": False})
    assert observer.record_build_selection(publication_id=102)
    terminal(db, 102, 302)
    assert project(observer, publication_id=102)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] == []
    with db.orm_session() as session:
        session.get(Publication, 102).status = "draft"
        session.get(Publication, 101).status = "online_pub"
    assert not project(observer, publication_id=101)
    terminal(db, 101, 303)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] is None
    assert project(observer, publication_id=101)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] == ["kb"]
    assert not project(observer, publication_id=102)
    with db.orm_session() as session:
        assert session.query(BotCommonConfig).count() == 3  # two snapshots + one marker
        assert session.query(Binding).one().enabled is False


def test_history_missing_is_unknown_even_with_enabled_draft_and_default_false_online(
    observation,
):
    db, observer, _ = observation
    terminal(db)
    assert not project(observer, publication_id=101)
    result = BotKnowledgeStageSelection(db).read(bot=source(db), runtime_stage="online")
    assert result["selection_status"] == "unrecorded" and result["kb_ids"] is None


@pytest.mark.parametrize("raw", ["not json", "{}", "null", '"text"'])
def test_corrupt_history_never_falls_back_to_draft(observation, raw):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        session.query(BotCommonConfig).one().config_value = raw
        session.get(Publication, 101).status = "validating"
    result = BotKnowledgeStageSelection(db).read(bot=source(db), runtime_stage="verify")
    assert result["selection_status"] == "unrecorded" and result["kb_ids"] is None


def test_snapshot_store_failure_is_unavailable_not_disabled(observation):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    with db.orm_session() as session:
        session.get(Publication, 101).status = "validating"
    BotCommonConfig.__table__.drop(db.factory.kw["bind"])
    assert (
        BotKnowledgeStageSelection(db).read(bot=source(db), runtime_stage="verify")[
            "selection_status"
        ]
        == "unavailable"
    )


def test_projection_write_failure_rolls_back_auxiliary_rows_and_native_success_stays(
    observation,
):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    engine = db.factory.kw["bind"]

    def fail_marker(conn, cursor, statement, *args):
        if "INSERT INTO ac_bot_common_config" in statement:
            raise RuntimeError("projection store failed")

    event.listen(engine, "before_cursor_execute", fail_marker)
    try:
        assert not project(observer, publication_id=101)
    finally:
        event.remove(engine, "before_cursor_execute", fail_marker)
    with db.orm_session() as session:
        assert session.get(Publication, 101).status == "success"
        assert session.query(Binding).one().online_enabled is False
        assert session.query(Binding).one().enabled is True


def test_late_projection_cannot_overwrite_later_workflow_marker(observation):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    terminal(db, workflow_id=400)
    assert project(observer, publication_id=101)
    terminal(db, workflow_id=399)
    assert not project(observer, publication_id=101)
    assert (
        BotKnowledgeStageSelection(db).read(bot=source(db), runtime_stage="online")[
            "kb_ids"
        ]
        is None
    )


def test_new_draft_coexists_with_online_but_not_with_verify(observation):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    assert project(observer, publication_id=101)
    with db.orm_session() as session:
        session.add(
            Publication(
                id=102,
                source_bot_pk=1,
                source_bot_id="bot",
                publish_bot_id="botpub2",
                name="Bot",
                owner_id="owner",
                status="draft",
                version=2,
                env="dev",
                permission_owner="owner",
            )
        )
    reader = BotKnowledgeStageSelection(db)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] == ["kb"]
    assert reader.read(bot=source(db), runtime_stage="verify")["kb_ids"] is None


def test_online_projection_is_independent_of_later_same_publication_build_sample(
    observation,
):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    assert project(observer, publication_id=101)
    with db.orm_session() as session:
        row = (
            session.query(BotCommonConfig)
            .filter(BotCommonConfig.config_key == "publication:101")
            .one()
        )
        value = json.loads(row.config_value)
        value["kb_ids"] = []
        row.config_value = json.dumps(value)
    assert BotKnowledgeStageSelection(db).read(bot=source(db), runtime_stage="online")[
        "kb_ids"
    ] == ["kb"]


@pytest.mark.parametrize("has_marker", [False, True])
def test_native_rollback_admission_does_not_resurrect_stale_projection(
    observation, has_marker
):
    """v2 projection failed; native rollback_flip alone must not expose v1."""
    from agentclaw.community.core.repository.implementations.publishing.bot_publish import (
        BotPublishRepository,
    )

    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    stale_observation = observer.observe_online_selection(publication_id=101)
    if has_marker:
        assert project(observer, publication_id=101)
    with db.orm_session() as session:
        session.get(Publication, 101).status = "upgraded"
        session.add(
            Publication(
                id=102,
                source_bot_pk=1,
                source_bot_id="bot",
                publish_bot_id="botpub2",
                name="Bot",
                owner_id="owner",
                status="success",
                version=2,
                last_pub_id=101,
                env="dev",
                permission_owner="owner",
            )
        )
    from unittest.mock import Mock
    from agentclaw.community.core.service_bot.repository.config_artifact_offload import (
        ConfigArtifactOffloader,
    )
    from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin

    repo = BotPublishRepository(
        db, ConfigArtifactOffloader(Mock(spec=ObjectStoragePlugin))
    )
    assert repo.rollback_flip(
        demoted_publish_id=102,
        demoted_ext={
            "rollback": {
                "rolled_back_at": "2026-11-02T12:00:00.123456",
                "target_publish_id": 101,
            }
        },
        demoted_from_status="success",
        demoted_to_status="draft",
        restored_publish_id=101,
        restored_ext={"publish": {"online": 301}, "rollback_restored_from": 102},
        restored_from_status="upgraded",
        restored_to_status="success",
    ) == (True, True)
    reader = BotKnowledgeStageSelection(db)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] is None
    assert not observer.project_online_selection(
        publication_id=101, observed=stale_observation
    )
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] is None
    with db.orm_session() as session:
        session.get(Publication, 101).ext = json.dumps(
            {"publish": {"online": 303}, "rollback_restored_from": 102}
        )
    assert project(observer, publication_id=101)
    assert reader.read(bot=source(db), runtime_stage="online")["kb_ids"] == ["kb"]


def test_projection_uses_transactional_port_even_when_ordinary_sessions_autocommit(
    observation,
):
    from contextlib import contextmanager

    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    calls = []

    class AutocommitBoundary:
        @contextmanager
        def orm_session(self):
            raise AssertionError(
                "ordinary AUTOCOMMIT session cannot protect multi-write projection"
            )
            yield

        @contextmanager
        def transactional_orm_session(self):
            calls.append("transaction")
            with db.transactional_orm_session() as session:
                yield session

    assert BotKnowledgeStageSelection(AutocommitBoundary()).project_online(
        publication_id=101,
        observed=observer.observe_online_selection(publication_id=101),
    )
    assert calls == ["transaction"]


@pytest.mark.parametrize("count", [1, 100])
def test_missing_projection_bindings_insert_in_one_bulk_call_with_explicit_tenant(
    observation, count
):
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    db, observer, _ = observation
    engine = db.factory.kw["bind"]
    with engine.begin() as connection:
        connection.execute(
            BotModel.__table__.update().values(avernet_tenant="external")
        )
        connection.execute(Binding.__table__.update().values(avernet_tenant="external"))
    with avernet_tenant_scope("external"):
        with db.orm_session() as session:
            session.add_all(
                Binding(
                    bot_pk=1,
                    space_id=1,
                    env="dev",
                    kb_id=f"selected-{i}",
                    enabled=True,
                    revision=1,
                    updated_by="owner",
                )
                for i in range(count - 1)
            )
        assert observer.record_build_selection(publication_id=101)
        with db.orm_session() as session:
            session.query(Binding).delete()
        terminal(db)
        inserts = []

        def record(conn, cursor, statement, *args):
            if statement.startswith("INSERT INTO ac_bot_knowledge_binding"):
                inserts.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            assert project(observer, publication_id=101)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert len(inserts) == 1
        with db.orm_session() as session:
            rows = session.query(Binding).all()
            assert len(rows) == count
            assert all(
                row.avernet_tenant == "external"
                and row.online_enabled
                and not row.enabled
                and row.revision == 0
                for row in rows
            )


def test_projection_replay_has_no_business_writes(observation):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    assert project(observer, publication_id=101)
    statements = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    engine = db.factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert project(observer, publication_id=101)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)


def test_hundred_online_ids_do_not_scan_disabled_history(observation):
    db, observer, _ = observation
    engine = db.factory.kw["bind"]
    with db.orm_session() as session:
        session.add_all(
            Binding(
                bot_pk=1,
                space_id=1,
                env="dev",
                kb_id=f"selected-{i}",
                enabled=True,
                revision=1,
                updated_by="owner",
            )
            for i in range(99)
        )
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    assert project(observer, publication_id=101)
    bot = source(db)
    counts = []
    for size in (1, 1000, 10000):
        with db.orm_session() as session:
            session.query(Binding).filter(Binding.kb_id.like("disabled-%")).delete()
            session.execute(
                Binding.__table__.insert(),
                [
                    dict(
                        avernet_tenant="teamclaw",
                        bot_pk=1,
                        space_id=1,
                        env="dev",
                        kb_id=f"disabled-{i}",
                        enabled=False,
                        online_enabled=False,
                        revision=1,
                        updated_by="owner",
                    )
                    for i in range(size)
                ],
            )
        steps = [0]

        def tick():
            steps[0] += 1
            return 0

        def checkout(connection, *args):
            connection.set_progress_handler(tick, 1)

        def checkin(connection, *args):
            connection.set_progress_handler(None, 0)

        event.listen(engine, "checkout", checkout)
        event.listen(engine, "checkin", checkin)
        try:
            assert (
                len(
                    BotKnowledgeStageSelection(db).read(
                        bot=bot, runtime_stage="online"
                    )["kb_ids"]
                )
                == 100
            )
        finally:
            event.remove(engine, "checkout", checkout)
            event.remove(engine, "checkin", checkin)
        counts.append(steps[0])
    assert max(counts) - min(counts) < 200, counts


def test_plain_restart_preserves_existing_publication_selection(observation):
    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    assert project(observer, publication_id=101)
    with db.orm_session() as session:
        session.get(Publication, 101).ext = json.dumps(
            {"publish": {"online": 301}, "restart": {"online": 305}}
        )
    # A restart is not a new publication and must not recopy/endorse snapshots.
    assert BotKnowledgeStageSelection(db).read(bot=source(db), runtime_stage="online")[
        "kb_ids"
    ] == ["kb"]


def test_concurrent_auxiliary_writers_never_leave_partial_projection(observation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    db, observer, _ = observation
    assert observer.record_build_selection(publication_id=101)
    terminal(db)
    observed = observer.observe_online_selection(publication_id=101)
    start = Barrier(2)

    def write():
        start.wait(timeout=10)
        return observer.project_online_selection(publication_id=101, observed=observed)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: write(), range(2)))
    assert any(outcomes)
    with db.orm_session() as session:
        assert (
            session.query(BotCommonConfig)
            .filter_by(config_key="projection:online")
            .count()
            == 1
        )
        assert session.query(Binding).one().online_enabled is True
        assert session.query(Binding).one().enabled is True
    assert BotKnowledgeStageSelection(db).read(bot=source(db), runtime_stage="online")[
        "kb_ids"
    ] == ["kb"]


def test_older_cross_version_writer_cannot_replace_newer_community_projection(
    observation,
):
    """SQLite ignores FOR UPDATE; auxiliary CAS must roll back the losing writer."""
    from threading import Event, Thread, current_thread

    from agentclaw.community.core.bot_knowledge.publication_selection import (
        BotKnowledgePublicationSelection,
    )
    from agentclaw.community.core.repository.implementations.config.bot_common_config import (
        BotCommonConfigRepository,
    )
    from agentclaw.community.plugins.community.database import CommunityDatabase

    db, initial, _ = observation
    assert initial.record_build_selection(publication_id=101)
    terminal(db)
    assert project(initial, publication_id=101)
    for publication_id, version, kb_id in ((102, 2, "v2-only"), (103, 3, "v3-only")):
        with db.orm_session() as session:
            session.get(Publication, 101).status = "upgraded"
            session.query(Binding).update({"enabled": False})
            session.add(
                Publication(
                    id=publication_id,
                    source_bot_pk=1,
                    source_bot_id="bot",
                    publish_bot_id=f"botpub{version}",
                    name="Bot",
                    owner_id="owner",
                    status="building",
                    version=version,
                    env="dev",
                    permission_owner="owner",
                )
            )
            session.add(
                Binding(
                    bot_pk=1,
                    space_id=1,
                    env="dev",
                    kb_id=kb_id,
                    enabled=True,
                    revision=1,
                    updated_by="owner",
                )
            )
        assert initial.record_build_selection(publication_id=publication_id)
    terminal(db, 102, 302)
    community = CommunityDatabase(str(db.factory.kw["bind"].url), create_schema=False)
    observer = BotKnowledgePublicationSelection(
        community, BotCommonConfigRepository(community)
    )
    paused, resume = Event(), Event()
    outcomes = {}

    def gate(conn, cursor, sql, *args):
        if (
            current_thread().name == "old-projection"
            and not paused.is_set()
            and sql.startswith(
                ("UPDATE ac_bot_knowledge_binding", "UPDATE ac_bot_common_config")
            )
        ):
            paused.set()
            assert resume.wait(15), "new projection did not complete"

    def old_writer():
        outcomes["old"] = project(observer, publication_id=102)

    event.listen(community._engine, "before_cursor_execute", gate)
    thread = Thread(target=old_writer, name="old-projection")
    thread.start()
    try:
        assert paused.wait(10), "old projection did not reach its write boundary"
        with db.orm_session() as session:
            session.get(Publication, 102).status = "upgraded"
        terminal(db, 103, 303)
        assert project(observer, publication_id=103)
    finally:
        resume.set()
        thread.join(15)
        event.remove(community._engine, "before_cursor_execute", gate)
        community._engine.dispose()
    assert not thread.is_alive()
    assert outcomes["old"] is False
    with db.orm_session() as session:
        marker = json.loads(
            session.query(BotCommonConfig)
            .filter_by(config_key="projection:online")
            .one()
            .config_value
        )
        assert marker["workflow_id"] == 303
        assert marker["selection"]["publication_id"] == 103
        assert sorted(
            row.kb_id for row in session.query(Binding).filter_by(online_enabled=True)
        ) == ["v3-only"]
        assert session.get(Publication, 103).status == "success"
    assert BotKnowledgeStageSelection(db).read(bot=source(db), runtime_stage="online")[
        "kb_ids"
    ] == ["v3-only"]
