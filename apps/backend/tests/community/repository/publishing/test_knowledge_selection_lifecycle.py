"""Publication repository behavior against the real binding/publish transaction."""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from tests.community.core.bot_knowledge.summary_fixtures import seed_online
from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel as Binding,
)
from agentclaw.community.core.repository.implementations.publishing.bot_publish import (
    BotPublishRepository,
)
from agentclaw.community.core.service_bot.repository.config_artifact_offload import (
    ConfigArtifactOffloader,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.kernel.knowledge import KnowledgePublicationPolicy
from agentclaw.community.plugin_api.models import BotModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class Database:
    def __init__(self, engine):
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def orm_session(self):
        with self.factory.begin() as session:
            yield session

    transactional_orm_session = orm_session


@pytest.fixture
def publication(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge-publish.db'}")
    Base.metadata.create_all(engine)
    db = Database(engine)
    with db.orm_session() as session:
        session.add(
            BotModel(
                id=1,
                bot_id="service",
                bot_name="Service",
                entity_id="owner",
                entity_type="staff",
                creator_id="owner",
                owner_id="owner",
                bot_type="service",
                space_id=9,
                env="dev",
                is_delete=0,
            )
        )
        session.add_all(
            [
                Binding(
                    bot_pk=1,
                    space_id=9,
                    env="dev",
                    kb_id=kb,
                    enabled=enabled,
                    revision=1,
                    updated_by="owner",
                )
                for kb, enabled in (("A", False), ("B", True))
            ]
        )
    repo = BotPublishRepository(
        db,
        offload=ConfigArtifactOffloader(MagicMock()),
        knowledge_policy=KnowledgePublicationPolicy(enabled=True),
    )
    record = repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.pub",
            name="Service",
            owner_id="owner",
            permission_owner="owner",
            status=PublishStatus.DRAFT,
            version=1,
            env="dev",
            ext={"other": 1},
        )
    )
    yield db, repo, record
    engine.dispose()


def test_begin_build_freezes_exact_current_selection(publication):
    _db, repo, record = publication
    built = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    snapshot = built.ext["knowledge_selection_snapshot"]
    assert snapshot["enabled_kb_ids"] == ["B"]
    assert snapshot["source_bot_pk"] == 1
    assert snapshot["space_id"] == 9
    assert snapshot["env"] == "dev"
    assert snapshot["avernet_tenant"] == "teamclaw"
    assert snapshot["publish_id"] == record.id
    assert snapshot["generation"]
    assert built.ext["other"] == 1
    assert (
        repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
        is None
    )
    assert repo.get_by_id(record.id).ext == built.ext


def _online(db):
    with db.orm_session() as session:
        return {
            r.kb_id
            for r in session.query(Binding)
            .filter(Binding.online_enabled.is_(True))
            .all()
        }


def _start_online(repo, record):
    building = repo.update_status(
        record.id, PublishStatus.BUILDING, PublishStatus.DRAFT
    )
    return repo.update_status_with_ext(
        record.id,
        PublishStatus.ONLINE_PUB,
        {**building.ext, "publish": {"online": 77}},
        source_status=PublishStatus.BUILDING,
    )


def test_only_successful_online_cas_replaces_production_from_frozen_selection(
    publication,
):
    db, repo, record = publication
    seed_online(db, record, ['A'])
    online = _start_online(repo, record)
    with db.orm_session() as session:
        session.query(Binding).filter(Binding.kb_id == "A").update(
            {Binding.online_enabled: True, Binding.enabled: True}
        )
        session.query(Binding).filter(Binding.kb_id == "B").update(
            {Binding.enabled: False}
        )
    assert _online(db) == {"A"}
    result = repo.compare_and_set_status_with_ext(
        publish_id=record.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=online.ext,
        ext={**online.ext, "done": True},
        observed_workflow_id=((online.ext or {}).get("publish") or {}).get("online", 77),
    )
    assert result.status == PublishStatus.SUCCESS
    assert _online(db) == {"B"}
    assert (
        repo.compare_and_set_status_with_ext(
            publish_id=record.id,
            source_status=PublishStatus.ONLINE_PUB,
            target_status=PublishStatus.SUCCESS,
            expected_ext=online.ext,
            ext=online.ext,
            observed_workflow_id=((online.ext or {}).get("publish") or {}).get("online", 77),
        )
        is None
    )
    assert _online(db) == {"B"}


def test_explicit_draft_restore_restores_only_draft_and_is_replay_safe(publication):
    db, repo, record = publication
    previous = _start_online(repo, record)
    repo.compare_and_set_status_with_ext(
        publish_id=record.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=previous.ext,
        ext=previous.ext,
        observed_workflow_id=((previous.ext or {}).get("publish") or {}).get("online", 77),
    )
    draft = repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.pub",
            name="Service",
            owner_id="owner",
            permission_owner="owner",
            status=PublishStatus.DRAFT,
            version=2,
            env="dev",
            last_pub_id=record.id,
        )
    )
    with db.orm_session() as session:
        session.query(Binding).filter(Binding.kb_id == "A").update(
            {Binding.enabled: True}
        )
        session.query(Binding).filter(Binding.kb_id == "B").update(
            {Binding.enabled: False}
        )
    from datetime import datetime, timedelta

    from agentclaw.community.core.repository.implementations.publishing.publish_operation import (
        OrmPublishOperationRepository,
    )

    operation = OrmPublishOperationRepository(db).insert(
        dict(
            publish_id=draft.id,
            operation_kind="draft_restore",
            stage="draft",
            request_id=f"restore_lifecycle_{draft.id}",
            attempt=1,
            params={
                "source_publish_id": record.id,
                "source_version": record.version,
                "deadline_at": (datetime.now() + timedelta(minutes=30)).isoformat(),
            },
        )
    )
    assert repo.restore_draft_knowledge_selection(
        draft_publish_id=draft.id,
        source_publish_id=record.id,
        operation_id=operation.id,
        expected_attempt=operation.attempt,
        final_result={"status": "success"},
    )
    with db.orm_session() as session:
        assert {
            r.kb_id
            for r in session.query(Binding).filter(Binding.enabled.is_(True)).all()
        } == {"B"}
        session.query(Binding).filter(Binding.kb_id == "A").update(
            {Binding.enabled: True}
        )
    assert _online(db) == {"B"}
    assert repo.restore_draft_knowledge_selection(
        draft_publish_id=draft.id,
        source_publish_id=record.id,
        operation_id=operation.id,
        expected_attempt=operation.attempt,
        final_result={"status": "success"},
    )
    with db.orm_session() as session:
        assert {
            r.kb_id
            for r in session.query(Binding).filter(Binding.enabled.is_(True)).all()
        } == {"A", "B"}


def test_missing_snapshot_completes_native_success_but_clears_old_online(
    publication,
):
    db, repo, record = publication
    seed_online(db, record, ['A'])
    online = repo.update_status_with_ext(
        record.id,
        PublishStatus.ONLINE_PUB,
        {"publish": {"online": 77}},
        source_status=PublishStatus.DRAFT,
    )
    with db.orm_session() as session:
        session.query(Binding).filter(Binding.kb_id == "A").update(
            {Binding.online_enabled: True}
        )
    completed = repo.compare_and_set_status_with_ext(
        publish_id=record.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=online.ext,
        ext=online.ext,
        observed_workflow_id=((online.ext or {}).get("publish") or {}).get("online", 77),
    )
    assert completed.status == PublishStatus.SUCCESS
    assert completed.ext["knowledge_selection_result"] == {
        "status": "unknown", "reason": "KNOWLEDGE_SNAPSHOT_MISSING", "deployment_id": 77, "owner_id": "owner",
    }
    assert _online(db) == set()

def test_empty_snapshot_is_valid_and_clears_production(publication):
    db, repo, record = publication
    seed_online(db, record, ['A', 'B'])
    with db.orm_session() as session:
        session.query(Binding).update(
            {Binding.online_enabled: True, Binding.enabled: False}
        )
    online = _start_online(repo, record)
    assert online.ext["knowledge_selection_snapshot"]["enabled_kb_ids"] == []
    repo.compare_and_set_status_with_ext(
        publish_id=record.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=online.ext,
        ext=online.ext,
        observed_workflow_id=((online.ext or {}).get("publish") or {}).get("online", 77),
    )
    assert _online(db) == set()


@pytest.mark.parametrize("change", ["space", "orphan", "environment", "tenant"])
def test_source_scope_change_never_promotes_same_named_bot(publication, change):
    db, repo, record = publication
    online = _start_online(repo, record)
    with db.orm_session() as session:
        source = session.get(BotModel, 1)
        if change == "space":
            source.space_id = 10
        elif change == "orphan":
            session.delete(source)
            session.flush()
            session.add(
                BotModel(
                    id=2,
                    bot_id="service",
                    bot_name="Replacement",
                    entity_id="owner",
                    entity_type="staff",
                    owner_id="owner",
                    creator_id="owner",
                    bot_type="service",
                    space_id=9,
                    env="dev",
                    is_delete=0,
                )
            )
        elif change == "environment":
            source.env = "pre"
        else:
            source.avernet_tenant = "other"
    completed = repo.compare_and_set_status_with_ext(
        publish_id=record.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=online.ext,
        ext=online.ext,
        observed_workflow_id=((online.ext or {}).get("publish") or {}).get("online", 77),
    )
    assert completed.status == PublishStatus.SUCCESS
    assert completed.ext["knowledge_selection_result"]["status"] == "unknown"
    assert _online(db) == set()

def test_legacy_ext_updates_cannot_drop_snapshot_and_stale_outcome_does_not_promote(
    publication,
):
    db, repo, record = publication
    online = _start_online(repo, record)
    updated = repo.update_status_with_ext(
        record.id,
        PublishStatus.ONLINE_PUB,
        {"publish": {"online": 78}, "background": True},
        source_status=PublishStatus.ONLINE_PUB,
    )
    assert (
        updated.ext["knowledge_selection_snapshot"]
        == online.ext["knowledge_selection_snapshot"]
    )
    assert (
        repo.compare_and_set_status_with_ext(
            publish_id=record.id,
            source_status=PublishStatus.ONLINE_PUB,
            target_status=PublishStatus.SUCCESS,
            expected_ext=online.ext,
            ext=online.ext,
            observed_workflow_id=((online.ext or {}).get("publish") or {}).get("online", 77),
        )
        is None
    )
    assert _online(db) == set()


def test_failed_build_retry_keeps_snapshot_but_redraft_refreezes_new_generation(
    publication,
):
    db, repo, record = publication
    building = repo.update_status(
        record.id, PublishStatus.BUILDING, PublishStatus.DRAFT
    )
    old_snapshot = building.ext["knowledge_selection_snapshot"]
    failed = repo.update_status(record.id, PublishStatus.FAILED, PublishStatus.BUILDING)
    retry = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.FAILED)
    assert retry.ext["knowledge_selection_snapshot"] == old_snapshot
    repo.update_status(record.id, PublishStatus.DRAFT, PublishStatus.BUILDING)
    with db.orm_session() as session:
        session.query(Binding).update({Binding.enabled: False})
    next_build = repo.update_status(
        record.id, PublishStatus.BUILDING, PublishStatus.DRAFT
    )
    assert (
        next_build.ext["knowledge_selection_snapshot"]["generation"]
        != old_snapshot["generation"]
    )
    assert next_build.ext["knowledge_selection_snapshot"]["enabled_kb_ids"] == []
    assert (
        repo.compare_and_set_status_with_ext(
            publish_id=record.id,
            source_status=PublishStatus.ONLINE_PUB,
            target_status=PublishStatus.SUCCESS,
            expected_ext=failed.ext,
            ext=failed.ext,
            observed_workflow_id=((failed.ext or {}).get("publish") or {}).get("online", 77),
        )
        is None
    )
    assert _online(db) == set()


def test_rollback_flip_does_not_promote_until_target_deployment_succeeds(publication):
    db, repo, first = publication
    target = _start_online(repo, first)
    repo.compare_and_set_status_with_ext(
        publish_id=first.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=target.ext,
        ext=target.ext,
        observed_workflow_id=((target.ext or {}).get("publish") or {}).get("online", 77),
    )
    second = repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.pub",
            name="Service",
            owner_id="owner",
            permission_owner="owner",
            status=PublishStatus.DRAFT,
            version=2,
            env="dev",
            last_pub_id=first.id,
        )
    )
    with db.orm_session() as session:
        session.query(Binding).filter(Binding.kb_id == "A").update(
            {Binding.enabled: True}
        )
        session.query(Binding).filter(Binding.kb_id == "B").update(
            {Binding.enabled: False}
        )
    current = _start_online(repo, second)
    current = repo.compare_and_set_status_with_ext(
        publish_id=second.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=current.ext,
        ext=current.ext,
        observed_workflow_id=((current.ext or {}).get("publish") or {}).get("online", 77),
    )
    target = repo.update_status(first.id, PublishStatus.UPGRADED, PublishStatus.SUCCESS)
    assert _online(db) == {"A"}
    assert repo.rollback_flip(
        demoted_publish_id=second.id,
        demoted_ext=current.ext,
        demoted_from_status=PublishStatus.SUCCESS,
        demoted_to_status=PublishStatus.DRAFT,
        restored_publish_id=first.id,
        restored_ext=target.ext,
        restored_from_status=PublishStatus.UPGRADED,
        restored_to_status=PublishStatus.SUCCESS,
    ) == (True, True)
    assert _online(db) == {"A"}
    target = repo.update_status_with_ext(
        first.id,
        PublishStatus.ONLINE_PUB,
        {**target.ext, "publish": {"online": 99}},
        source_status=PublishStatus.SUCCESS,
    )
    assert _online(db) == {"A"}
    repo.compare_and_set_status_with_ext(
        publish_id=first.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=target.ext,
        ext=target.ext,
        observed_workflow_id=((target.ext or {}).get("publish") or {}).get("online", 77),
    )
    assert _online(db) == {"B"}


def test_unconfigured_legacy_source_does_not_break_plain_publish_or_restore(
    publication,
):
    db, repo, record = publication
    with db.orm_session() as session:
        session.query(Binding).delete()
        session.query(BotModel).filter(BotModel.id == 1).update(
            {BotModel.space_id: None}
        )
    legacy = repo.update_status_with_ext(
        record.id,
        PublishStatus.ONLINE_PUB,
        {"publish": {"online": 77}},
        source_status=PublishStatus.DRAFT,
    )
    assert (
        repo.compare_and_set_status_with_ext(
            publish_id=record.id,
            source_status=PublishStatus.ONLINE_PUB,
            target_status=PublishStatus.SUCCESS,
            expected_ext=legacy.ext,
            ext=legacy.ext,
            observed_workflow_id=((legacy.ext or {}).get("publish") or {}).get("online", 77),
        ).status
        == PublishStatus.SUCCESS
    )
    draft = repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.pub",
            name="Service",
            owner_id="owner",
            permission_owner="owner",
            status=PublishStatus.DRAFT,
            version=2,
            env="dev",
            last_pub_id=record.id,
        )
    )
    from datetime import datetime, timedelta

    from agentclaw.community.core.repository.implementations.publishing.publish_operation import (
        OrmPublishOperationRepository,
    )

    operation = OrmPublishOperationRepository(db).insert(
        dict(
            publish_id=draft.id,
            operation_kind="draft_restore",
            stage="draft",
            request_id=f"restore_lifecycle_{draft.id}",
            attempt=1,
            params={
                "source_publish_id": record.id,
                "source_version": record.version,
                "deadline_at": (datetime.now() + timedelta(minutes=30)).isoformat(),
            },
        )
    )
    assert repo.restore_draft_knowledge_selection(
        draft_publish_id=draft.id,
        source_publish_id=record.id,
        operation_id=operation.id,
        expected_attempt=operation.attempt,
        final_result={"status": "success"},
    )
    assert _online(db) == set()


def test_failed_online_outcome_keeps_previous_production_selection(publication):
    db, repo, record = publication
    seed_online(db, record, ['A'])
    online = _start_online(repo, record)
    with db.orm_session() as session:
        session.query(Binding).filter(Binding.kb_id == "A").update(
            {Binding.online_enabled: True}
        )
    result = repo.compare_and_set_status_with_ext(
        publish_id=record.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.FAILED,
        expected_ext=online.ext,
        ext={**online.ext, "error_message": "deploy failed"},
    )
    assert result.status == PublishStatus.FAILED
    assert _online(db) == {"A"}


def test_snapshot_capacity_is_checked_before_accepting_build(publication):
    from agentclaw.community.kernel.knowledge import KnowledgeError

    db, repo, record = publication
    repo.update_status_with_ext(
        record.id,
        PublishStatus.DRAFT,
        {"large": "中" * 21820},
        source_status=PublishStatus.DRAFT,
    )
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_PUBLISH_EXT_TOO_LARGE"):
        repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    assert repo.get_by_id(record.id).status == PublishStatus.DRAFT
    assert "knowledge_selection_snapshot" not in repo.get_by_id(record.id).ext


@pytest.mark.parametrize("first", ["publish", "edit"])
def test_sqlite_freeze_and_draft_edit_share_a_real_write_reservation(
    publication, first
):
    from threading import Event, Thread, current_thread

    from agentclaw.community.core.bot_knowledge.repository.store import (
        BotKnowledgeRepository,
    )
    from agentclaw.community.core.spaces.repository.models import (
        SpaceMemberModel,
        SpaceModel,
    )
    from agentclaw.community.kernel.knowledge import BotKnowledgeScope, KnowledgeError
    from sqlalchemy import event

    db, repo, record = publication
    with db.orm_session() as session:
        session.add(
            SpaceModel(
                id=9,
                space_code="team9",
                space_type="TEAM",
                name="Team",
                env="dev",
                created_by="owner",
                updated_by="owner",
            )
        )
        session.add(
            SpaceMemberModel(
                space_id=9,
                user_id="owner",
                role="MEMBER",
                env="dev",
                created_by="owner",
            )
        )
    engine = db.factory.kw["bind"]
    reserved, contending, release = Event(), Event(), Event()
    outcomes = {}

    def before(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            statement.startswith("UPDATE ac_bots SET id=")
            and current_thread().name != first
        ):
            contending.set()

    def after(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            statement.startswith("UPDATE ac_bots SET id=")
            and current_thread().name == first
        ):
            reserved.set()
            assert release.wait(5)

    def publish():
        try:
            outcomes["publish"] = repo.update_status(
                record.id, PublishStatus.BUILDING, PublishStatus.DRAFT
            )
        except Exception as error:
            outcomes["publish"] = error

    def edit():
        try:
            outcomes["edit"] = BotKnowledgeRepository(db).mutate(
                scope=BotKnowledgeScope("service", "owner", "dev"),
                actor_id="owner",
                kb_id="A",
                value=True,
                space_id=9,
                expected_revision=1,
            )
        except Exception as error:
            outcomes["edit"] = error

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    threads = {
        "publish": Thread(target=publish, name="publish"),
        "edit": Thread(target=edit, name="edit"),
    }
    try:
        threads[first].start()
        assert reserved.wait(5)
        threads["edit" if first == "publish" else "publish"].start()
        assert contending.wait(5)
    finally:
        release.set()
        for thread in threads.values():
            if thread.ident is not None:
                thread.join(10)
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
    assert all(not thread.is_alive() for thread in threads.values())
    assert not isinstance(outcomes["publish"], Exception), outcomes
    frozen = repo.get_by_id(record.id).ext["knowledge_selection_snapshot"][
        "enabled_kb_ids"
    ]
    if first == "publish":
        assert isinstance(outcomes["edit"], KnowledgeError), outcomes
        assert outcomes["edit"].code == "BOT_DRAFT_REQUIRED"
        assert frozen == ["B"]
    else:
        assert not isinstance(outcomes["edit"], Exception), outcomes
        assert frozen == ["A", "B"]


def test_late_rollback_success_cannot_overwrite_republished_newer_version(publication):
    from agentclaw.community.core.service_bot.repository.models import PublishOperationModel

    def deployed(publish_id, workflow_id, kind="upgrade"):
        with db.orm_session() as session:
            session.add(PublishOperationModel(
                publish_id=publish_id, operation_kind=kind, stage="online",
                attempt=workflow_id, request_id=f"workflow-{workflow_id}",
                bot_uuid="shared-online-bot", baas_publish_id=workflow_id,
                state="completed", env="dev",
            ))

    db, repo, first = publication

    def finish(record):
        return repo.compare_and_set_status_with_ext(
            publish_id=record.id,
            source_status=PublishStatus.ONLINE_PUB,
            target_status=PublishStatus.SUCCESS,
            expected_ext=record.ext,
            ext=record.ext,
            observed_workflow_id=((record.ext or {}).get("publish") or {}).get("online", 77),
        )

    first = finish(_start_online(repo, first))
    second = repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.pub",
            name="Service",
            owner_id="owner",
            permission_owner="owner",
            status=PublishStatus.DRAFT,
            version=2,
            env="dev",
            last_pub_id=first.id,
        )
    )
    with db.orm_session() as session:
        session.query(Binding).filter(Binding.kb_id == "A").update(
            {Binding.enabled: True}
        )
        session.query(Binding).filter(Binding.kb_id == "B").update(
            {Binding.enabled: False}
        )
    second = finish(_start_online(repo, second))
    first = repo.update_status(first.id, PublishStatus.UPGRADED, PublishStatus.SUCCESS)
    repo.rollback_flip(
        demoted_publish_id=second.id,
        demoted_ext=second.ext,
        demoted_from_status=PublishStatus.SUCCESS,
        demoted_to_status=PublishStatus.DRAFT,
        restored_publish_id=first.id,
        restored_ext={**first.ext, "rollback_restored_from": second.id},
        restored_from_status=PublishStatus.UPGRADED,
        restored_to_status=PublishStatus.SUCCESS,
    )
    rollback = repo.get_by_id(first.id)
    rollback = repo.update_status_with_ext(
        first.id,
        PublishStatus.ONLINE_PUB,
        {**rollback.ext, "publish": {"online": 99}},
        source_status=PublishStatus.SUCCESS,
    )
    # Native rollback left the demoted version in DRAFT. Republish that same id
    # before the older rollback's deployment callback returns.
    deployed(first.id, 99, "rollback_deploy")
    second = _start_online(repo, repo.get_by_id(second.id))
    second = repo.update_status_with_ext(
        second.id, PublishStatus.ONLINE_PUB,
        {**second.ext, "publish": {"online": 100}}, PublishStatus.ONLINE_PUB,
    )
    deployed(second.id, 100)
    second = finish(second)
    assert _online(db) == {"A"}
    late = finish(rollback)
    # The late rollback completes its native ledger; only its knowledge result
    # is fenced and the republished newer version keeps its flags.
    assert late is not None
    assert late.status == PublishStatus.SUCCESS
    result = late.ext["knowledge_selection_result"]
    assert result["status"] == "unknown"
    assert result["deployment_id"] == 99
    assert _online(db) == {"A"}


def _real_flow(repo):
    from agentclaw.community.core.service_bot.services.bot_publish_service import (
        BotPublishService,
    )
    from agentclaw.community.core.service_bot.services.publish_flow_service import (
        PublishFlowService,
    )

    bot_service, device_repo, queue = MagicMock(), MagicMock(), MagicMock()
    bot_service.get_bot.return_value = {"active_engine": "teclaw", "entity_id": "owner"}
    # dev baseline resolves the draft-restore source Bot through the internal
    # address lookup instead of the current-Owner get_bot contract.
    bot_service.get_bot_by_address.return_value = {
        "active_engine": "teclaw",
        "entity_id": "owner",
    }
    publish = BotPublishService(
        repo,
        MagicMock(),
        MagicMock(),
        bot_service,
        device_repo,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        queue,
        MagicMock(),
        MagicMock(),
    )
    flow = PublishFlowService(
        publish,
        MagicMock(),
        MagicMock(),
        bot_service,
        MagicMock(),
        MagicMock(),
        resolver=MagicMock(),
        device_fs_dispatcher=MagicMock(),
        teclaw_file_promotion=MagicMock(),
        device_binding_repo=device_repo,
        channel_overrides_reader=MagicMock(),
        task_queue_service=queue,
        publish_operation_repo=MagicMock(),
        runtime_projector=MagicMock(),
        runtime_layout_probe=MagicMock(),
    )
    return flow, queue
