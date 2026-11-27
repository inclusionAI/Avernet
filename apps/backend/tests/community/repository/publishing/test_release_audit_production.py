"""Audit failures never abort committed native publication writes."""
from contextlib import contextmanager
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.publishing.bot_publish import BotPublishRepository
from agentclaw.community.core.service_bot.repository.config_artifact_offload import ConfigArtifactOffloader
from agentclaw.community.plugin_api.models import Base, BotModel
from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditModel
from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit


class Database:
    def __init__(self, engine):
        self.factory = sessionmaker(engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        with self.factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    transactional_orm_session = orm_session


@pytest.fixture
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    Base.metadata.create_all(engine)
    database = Database(engine)
    with database.orm_session() as session:
        session.add(BotModel(id=1, bot_id="bot-a", bot_name="A", entity_id="stable-entity",
                             entity_type="staff", creator_id="original-creator", owner_id="new-owner",
                             bot_type="service", active_engine="teclaw", status="ACTIVE", env="dev"))
    return BotPublishRepository(database, ConfigArtifactOffloader(None), DatabaseServiceBotReleaseAudit()), engine


def create(repository):
    return repository[0].insert(dict(source_bot_pk=1, source_bot_id="bot-a", publish_bot_id="bot-a",
                                    name="A", owner_id="stable-entity", permission_owner="owner",
                                    version=1, env="dev", status="draft", audit_operator_id="creator-delegate"))


def audits(repository):
    with repository[0]._db.orm_session() as session:
        rows = session.query(ReleaseAuditModel).execution_options(skip_avernet_tenant_guard=True).order_by(ReleaseAuditModel.id).all()
        session.expunge_all()
        return rows


def test_draft_creation_records_original_creator_and_actor(repository):
    record = create(repository)
    produced = audits(repository)
    assert len(produced) == 1
    snapshot = produced[0]
    assert snapshot.publish_id == record.id
    assert snapshot.creator_id == "original-creator"
    assert snapshot.operator_id == "creator-delegate"
    assert snapshot.desired_stage == "draft"
    assert snapshot.finish_status == "succeeded"
    assert snapshot.submitted_at is None
    assert snapshot.finished_at is not None
    assert snapshot.env == "dev"


def test_submission_and_terminal_share_change_and_frozen_actor(repository):
    repo, _ = repository
    record = create(repository)
    started = repo.update_status(record.id, "building", "draft", audit_operator_id="submitter")
    original = audits(repository)[-1]
    assert original.finish_status == "pending" and original.finished_at is None
    before = started.ext
    repo.compare_and_set_status_with_ext(publish_id=record.id, source_status="building",
                                        target_status="validate_pub", expected_ext=before,
                                        ext={**before, "publish": {"verify": 101}})
    before = repo.get_by_id(record.id).ext
    with repo._db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 1).update({"creator_id": "later-change"})
    repo.compare_and_set_status_with_ext(publish_id=record.id, source_status="validate_pub",
                                        target_status="validating", expected_ext=before, ext=before)
    rows = audits(repository)
    assert len(rows) == 2
    terminal = rows[-1]
    assert terminal.id == original.id and terminal.change_id == original.change_id
    assert terminal.creator_id == "original-creator"
    assert terminal.operator_id == "submitter"
    assert terminal.finish_status == "succeeded"
    assert terminal.final_publish_status == "validating"
    assert terminal.submitted_at == original.submitted_at


def test_build_failure_and_retry_create_separate_changes(repository):
    repo, _ = repository
    record = create(repository)
    started = repo.update_status(record.id, "building", "draft", audit_operator_id="submitter")
    repo.compare_and_set_status_with_ext(publish_id=record.id, source_status="building",
                                        target_status="failed", expected_ext=started.ext, ext=started.ext)
    failed = audits(repository)[-1]
    assert failed.finish_status == "failed"
    retried = repo.update_status(record.id, "building", "failed", audit_operator_id="retry-actor")
    retry_event = audits(repository)[-1]
    assert retry_event.change_id != failed.change_id
    assert retry_event.operator_id == "retry-actor"
    # A late response from the first attempt cannot match the new ext fence.
    assert repo.compare_and_set_status_with_ext(
        publish_id=record.id, source_status="building", target_status="failed",
        expected_ext=started.ext, ext=started.ext,
    ) is None
    assert repo.get_by_id(record.id).status == "building"
    assert audits(repository)[-1].change_id == retry_event.change_id
    assert retried.ext != started.ext


def test_online_submission_and_completion_keep_environment(repository):
    repo, _ = repository
    record = create(repository)
    repo.update_status(record.id, "validating", "draft")
    started = repo.update_status(record.id, "online_pub", "validating", audit_operator_id="deployer")
    repo.compare_and_set_status_with_ext(publish_id=record.id, source_status="online_pub",
                                        target_status="success", expected_ext=started.ext, ext=started.ext)
    terminal = audits(repository)[-1]
    assert terminal.desired_stage == "online"
    assert terminal.env == "dev"
    assert terminal.finish_status == "succeeded"
    assert terminal.final_publish_status == "success"


def test_rejected_status_update_does_not_create_audit(repository):
    repo, _ = repository
    record = create(repository)
    assert repo.update_status(record.id, "building", "failed", audit_operator_id="submitter") is None
    assert len(audits(repository)) == 1


def test_audit_insert_failure_preserves_publication_status(repository):
    from sqlalchemy import event
    repo, _ = repository
    record = create(repository)

    def fail_insert(*args):
        raise RuntimeError("audit unavailable")

    event.listen(ReleaseAuditModel, "before_insert", fail_insert)
    try:
        started = repo.update_status(record.id, "building", "draft", audit_operator_id="submitter")
    finally:
        event.remove(ReleaseAuditModel, "before_insert", fail_insert)
    assert repo.get_by_id(record.id).status == "building"
    assert started.ext["_release_audit_change_id"] not in {row.change_id for row in audits(repository)}
    assert len(audits(repository)) == 1


def test_unattributed_legacy_writes_do_not_invent_actor(repository):
    repo, _ = repository
    record = create(repository)
    repo.update_status(record.id, "building", "draft")
    assert len(audits(repository)) == 1


def test_retry_to_stable_success_waits_for_restart_confirmation(repository):
    repo, _ = repository
    record = create(repository)
    repo.update_status(record.id, "failed", "draft")
    started = repo.update_status_with_ext(record.id, "success", {"retry": True}, "failed",
                                          audit_operator_id="retry-actor")
    assert audits(repository)[-1].finish_status == "pending"
    cid = audits(repository)[-1].change_id
    pending = repo.compare_and_set_ext(publish_id=record.id, expected_ext=started.ext,
                                      ext={**started.ext, "restart": {"restarting": True}})
    assert audits(repository)[-1].finish_status == "pending"
    complete_ext = {**pending.ext, "restart": {}, "_release_audit_restart_completed": cid}
    repo.compare_and_set_ext(publish_id=record.id, expected_ext=pending.ext, ext=complete_ext)
    assert audits(repository)[-1].finish_status == "succeeded"
    assert audits(repository)[-1].change_id == cid
    assert audits(repository)[-1].finish_status == "succeeded"


def test_late_restart_success_does_not_finish_new_retry(repository):
    from unittest.mock import Mock
    from agentclaw.community.core.service_bot.services.publish_flow.progress_sync_mixin import ProgressSyncMixin
    from agentclaw.community.core.service_bot.services.publish_flow.stage_status_mixin import StageStatusMixin

    repo, _ = repository
    record = create(repository)
    repo.update_status(record.id, "failed", "draft")
    old = repo.update_status_with_ext(record.id, "success", {"retry": True, "restart": {"online": 101}},
                                      "failed", audit_operator_id="first")

    class Flow(ProgressSyncMixin, StageStatusMixin):
        get_publish_record = staticmethod(repo.get_by_id)
        _activate_binding = Mock()
        _refresh_provider_mcp_after_success = Mock()
        _publish_operation_repo = Mock()

        def get_baas_publish_progress(self, **kwargs):
            assert kwargs["baas_publish_id"] == 101
            repo.update_status(record.id, "failed", "success")
            repo.update_status_with_ext(record.id, "success", {"retry": True, "restart": {"online": 202}},
                                        "failed", audit_operator_id="second")
            return {"status": "SUCCESS"}

        def _mutate_and_update_ext(self, publish_id, mutator):
            before = repo.get_by_id(publish_id).ext
            import copy
            after = copy.deepcopy(before)
            mutator(after)
            return repo.compare_and_set_ext(publish_id=publish_id, expected_ext=before, ext=after).ext

    flow = Flow()
    flow._publish_operation_repo.get_latest_by_kind.return_value = None
    flow.sync_restart_progress(record.id)
    assert audits(repository)[-1].operator_id == "second"
    assert audits(repository)[-1].finish_status == "pending"
    latest = repo.get_by_id(record.id).ext
    assert latest.get("_release_audit_restart_completed") != latest["_release_audit_change_id"]
    assert old.ext["_release_audit_change_id"] != latest["_release_audit_change_id"]


@pytest.mark.asyncio
async def test_retry_submission_failure_records_failed_result_without_changing_status(repository):
    from agentclaw.community.core.service_bot.services.publish_flow.retry_ops_mixin import RetryOpsMixin
    from agentclaw.community.core.service_bot.services.publish_flow.stage_status_mixin import StageStatusMixin
    from agentclaw.community.core.service_bot.repository.models import PublishStatus

    repo, _ = repository
    record = create(repository)
    repo.update_status(record.id, "failed", "draft")
    repo.update_status_with_ext(record.id, "success", {"retry": True}, "failed", audit_operator_id="retry")

    class Flow(RetryOpsMixin, StageStatusMixin):
        _get_latest_ext = staticmethod(lambda publish_id: repo.get_by_id(publish_id).ext)

        async def execute_restart(self, **kwargs):
            return {"success": False, "message": "provider rejected submission"}

        @staticmethod
        def _clear_retry_flag(ext):
            ext.pop("retry", None)

        def _mutate_and_update_ext(self, publish_id, mutator):
            import copy
            before = repo.get_by_id(publish_id).ext
            after = copy.deepcopy(before)
            mutator(after)
            return repo.compare_and_set_ext(publish_id=publish_id, expected_ext=before, ext=after).ext

    await Flow()._retry_via_restart(record.id, "retry", PublishStatus.SUCCESS)
    terminal = audits(repository)[-1]
    assert terminal.finished_at is not None
    assert terminal.finish_status == "failed"
    assert terminal.final_publish_status == "success"
    assert repo.get_by_id(record.id).status == "success"


def test_external_tenant_submission_finishes_in_default_background_context(repository):
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
    repo, _ = repository
    with avernet_tenant_scope("external-a"):
        with repo._db.orm_session() as session:
            session.add(BotModel(id=2, bot_id="external-bot", bot_name="External",
                                 entity_id="external-entity", entity_type="staff", creator_id="creator-a",
                                 owner_id="owner-a", bot_type="service", active_engine="teclaw",
                                 status="ACTIVE", env="dev"))
        record = repo.insert(dict(source_bot_pk=2, source_bot_id="external-bot", publish_bot_id="external-bot",
                                  name="External", owner_id="external-entity", permission_owner="owner",
                                  version=1, env="dev", status="draft", audit_operator_id="external-actor"))
        started = repo.update_status(record.id, "building", "draft", audit_operator_id="external-actor")
    # Existing publication tasks restore no request tenant; trusted submission
    # identity must carry the original one through terminal event production.
    repo.compare_and_set_status_with_ext(publish_id=record.id, source_status="building", target_status="failed",
                                        expected_ext=started.ext, ext=started.ext)
    terminal = audits(repository)[-1]
    assert terminal.avernet_tenant == "external-a"
    assert terminal.operator_id == "external-actor"
    assert terminal.finish_status == "failed"


def test_autocommit_native_session_is_preserved_and_audit_failure_is_isolated(repository):
    from sqlalchemy import event
    repo, engine = repository

    class AutocommitDatabase(Database):
        def __init__(self):
            self.factory = sessionmaker(engine.execution_options(isolation_level="AUTOCOMMIT"), autoflush=False)
            self.transactions = 0

        @contextmanager
        def transactional_orm_session(self):
            self.transactions += 1
            factory = sessionmaker(engine.execution_options(isolation_level="SERIALIZABLE"), autoflush=False)
            with factory() as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

    db = AutocommitDatabase()
    repo._db = db
    record = create(repository)
    assert db.transactions == 1

    def fail_insert(*args):
        raise RuntimeError("audit unavailable")

    event.listen(ReleaseAuditModel, "before_insert", fail_insert)
    try:
        started = repo.update_status(record.id, "building", "draft", audit_operator_id="submitter")
    finally:
        event.remove(ReleaseAuditModel, "before_insert", fail_insert)
    assert db.transactions == 2
    assert repo.get_by_id(record.id).status == "building"
    assert started.ext["_release_audit_change_id"] not in {row.change_id for row in audits(repository)}
    assert len(audits(repository)) == 1


@pytest.mark.parametrize("artifact_size", [4, 62000])
def test_audit_change_marker_preserves_artifact_serialization_cas(repository, artifact_size):
    repo, _ = repository

    class Storage:
        def __init__(self):
            self.objects = {}

        def put_object(self, key, content):
            self.objects[key] = content
            return True

        def get_object(self, key):
            return self.objects[key]

    repo._offload = ConfigArtifactOffloader(Storage())
    record = create(repository)
    repo.update_status_with_ext(record.id, "draft", {"config_artifact": {"body": "x" * artifact_size}}, "draft")
    started = repo.update_status(record.id, "building", "draft", audit_operator_id="submitter")
    finished = repo.compare_and_set_status_with_ext(publish_id=record.id, source_status="building",
                                                   target_status="failed", expected_ext=started.ext, ext=started.ext)
    assert finished is not None
    assert finished.ext["config_artifact"]["body"] == "x" * artifact_size
    assert audits(repository)[-1].finish_status == "failed"


def test_audit_completion_failure_preserves_native_status_and_pending_audit(repository):
    from sqlalchemy import event
    repo, engine = repository
    record = create(repository)
    started = repo.update_status(record.id, "building", "draft", audit_operator_id="submitter")

    def fail_audit_update(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("UPDATE ac_service_bot_release_audit SET"):
            raise RuntimeError("audit update unavailable")

    event.listen(engine, "before_cursor_execute", fail_audit_update)
    try:
        repo.compare_and_set_status_with_ext(publish_id=record.id, source_status="building",
                                                target_status="failed", expected_ext=started.ext, ext=started.ext)
    finally:
        event.remove(engine, "before_cursor_execute", fail_audit_update)
    assert repo.get_by_id(record.id).status == "failed"
    assert audits(repository)[-1].finish_status == "pending"
    assert audits(repository)[-1].finished_at is None


def test_completed_audit_time_is_not_rewritten_by_later_metadata(repository):
    repo, _ = repository
    record = create(repository)
    started = repo.update_status(record.id, "building", "draft", audit_operator_id="submitter")
    done = repo.compare_and_set_status_with_ext(publish_id=record.id, source_status="building",
                                              target_status="failed", expected_ext=started.ext, ext=started.ext)
    original = audits(repository)[-1]
    repo.compare_and_set_ext(publish_id=record.id, expected_ext=done.ext, ext={**done.ext, "note": "later"})
    rows = audits(repository)
    assert len(rows) == 2
    assert rows[-1].id == original.id and rows[-1].finished_at == original.finished_at
    assert rows[-1].operator_id == "submitter"


def test_missing_plugin_preserves_native_writes(repository, caplog):
    repo, _ = repository
    legacy = BotPublishRepository(repo._db, repo._offload)
    record = create(repository)
    started = repo.update_status(record.id, "building", "draft", audit_operator_id="editor")
    done = legacy.compare_and_set_status_with_ext(
        publish_id=record.id, source_status="building", target_status="failed",
        expected_ext=started.ext, ext=started.ext)
    assert done.status == "failed"
    assert audits(repository)[-1].finish_status == "pending"
    retried = legacy.update_status(record.id, "building", "failed", audit_operator_id="editor")
    assert retried.status == "building"
    assert retried.ext["_release_audit_change_id"] != done.ext["_release_audit_change_id"]
    assert "release_audit.skipped" in caplog.text


@pytest.mark.parametrize("failure", ["missing_table", "invalid_snapshot", "partial_insert"])
def test_audit_create_failure_keeps_native_draft(repository, failure, caplog):
    repo, engine = repository
    if failure == "missing_table":
        ReleaseAuditModel.__table__.drop(engine)
    elif failure == "invalid_snapshot":
        with repo._db.orm_session() as session:
            session.query(BotModel).filter(BotModel.id == 1).delete()
    else:
        class PartialPlugin(DatabaseServiceBotReleaseAudit):
            def create(self, session, snapshot):
                super().create(session, snapshot)
                raise RuntimeError("private payload must not be logged")
        repo._audit = PartialPlugin()
    record = create(repository)
    assert repo.get_by_id(record.id).status == "draft"
    assert "release_audit.skipped" in caplog.text
    assert "private payload must not be logged" not in caplog.text
    if failure != "missing_table":
        assert audits(repository) == []


def test_failed_submission_audit_replaces_previous_change_marker(repository):
    repo, _ = repository
    record = create(repository)
    started = repo.update_status(record.id, "building", "draft", audit_operator_id="first")
    repo.update_status(record.id, "failed", "building")
    class PartialPlugin(DatabaseServiceBotReleaseAudit):
        def create(self, session, snapshot):
            super().create(session, snapshot)
            raise RuntimeError("unavailable")
    repo._audit = PartialPlugin()
    retried = repo.update_status_with_ext(record.id, "building", {**started.ext, "native": 1},
                                          "failed", audit_operator_id="second")
    assert retried.status == "building"
    assert retried.ext["native"] == 1
    assert retried.ext["_release_audit_change_id"] != started.ext["_release_audit_change_id"]
    assert len(audits(repository)) == 2
    assert audits(repository)[-1].operator_id == "first"


def test_native_flush_error_still_rolls_back_publication(repository):
    from sqlalchemy import event
    repo, _ = repository
    def fail_native(*args):
        raise RuntimeError("native unavailable")
    event.listen(repo.Model, "before_insert", fail_native)
    try:
        with pytest.raises(RuntimeError, match="native unavailable"):
            create(repository)
    finally:
        event.remove(repo.Model, "before_insert", fail_native)
    assert audits(repository) == []


@pytest.mark.parametrize("target", ["validating", "success", "failed", "released"])
def test_missing_audit_table_does_not_block_completion(repository, target):
    repo, engine = repository
    record = create(repository)
    started = repo.update_status(record.id, "building", "draft", audit_operator_id="editor")
    ReleaseAuditModel.__table__.drop(engine)
    done = repo.compare_and_set_status_with_ext(
        publish_id=record.id, source_status="building", target_status=target,
        expected_ext=started.ext, ext={**started.ext, "native": "kept"})
    assert done.status == target
    assert done.ext["native"] == "kept"


def test_partial_completion_failure_keeps_pending_audit_and_native_ext(repository):
    repo, _ = repository
    record = create(repository)
    started = repo.update_status(record.id, "building", "draft", audit_operator_id="editor")
    class PartialPlugin(DatabaseServiceBotReleaseAudit):
        def complete(self, session, **kwargs):
            super().complete(session, **kwargs)
            raise RuntimeError("unavailable")
    repo._audit = PartialPlugin()
    done = repo.compare_and_set_ext(
        publish_id=record.id, expected_ext=started.ext, ext={**started.ext, "native": "kept"})
    done = repo.update_status(done.id, "validating", "building")
    assert done.status == "validating" and done.ext["native"] == "kept"
    assert audits(repository)[-1].finish_status == "pending"
    assert audits(repository)[-1].finished_at is None


def test_audit_failures_cannot_finish_the_previous_attempt(repository):
    repo, _ = repository
    record = create(repository)
    first = repo.update_status(record.id, "building", "draft", audit_operator_id="first")
    class BrokenPlugin(DatabaseServiceBotReleaseAudit):
        def create(self, session, snapshot):
            raise RuntimeError("insert unavailable")
        def complete(self, session, **kwargs):
            raise RuntimeError("update unavailable")
    repo._audit = BrokenPlugin()
    repo.update_status(record.id, "failed", "building")
    second = repo.update_status(record.id, "building", "failed", audit_operator_id="second")
    assert second.ext["_release_audit_change_id"] != first.ext["_release_audit_change_id"]
    repo._audit = DatabaseServiceBotReleaseAudit()
    assert repo.update_status(record.id, "validating", "building").status == "validating"
    assert audits(repository)[-1].operator_id == "first"
    assert audits(repository)[-1].finish_status == "pending"


def test_audit_transaction_failure_happens_after_native_commit(repository, caplog):
    repo, _ = repository
    original = repo._db.transactional_orm_session
    calls = 0
    @contextmanager
    def fail_audit_transaction():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("audit connection unavailable")
        with original() as session:
            yield session
    repo._db.transactional_orm_session = fail_audit_transaction
    record = create(repository)
    assert calls == 1 and record.status == "draft"
    assert repo.get_by_id(record.id).status == "draft"
    assert audits(repository) == []
    assert "release_audit.skipped" in caplog.text


def test_stale_post_commit_observation_never_audits_newer_state(repository, caplog):
    from agentclaw.community.core.repository.implementations.publishing.release_audit import record_transition
    repo, _ = repository
    record = create(repository)
    first = repo.update_status(record.id, "building", "draft", audit_operator_id="first")
    repo.update_status(record.id, "failed", "building")
    second = repo.update_status(record.id, "building", "failed", audit_operator_id="second")
    record_transition(repo._db, record.id, status="building", ext=json.dumps(first.ext),
                      source_status="draft", actor="first", new_change_id=first.ext["_release_audit_change_id"],
                      audit=repo._audit)
    assert len(audits(repository)) == 3
    assert audits(repository)[-1].change_id == second.ext["_release_audit_change_id"]
    assert audits(repository)[-1].finish_status == "pending"


def test_plugin_observes_native_commit_from_another_connection(repository):
    repo, _ = repository
    class ObservingPlugin(DatabaseServiceBotReleaseAudit):
        def create(self, session, snapshot):
            with repo._db.orm_session() as other:
                native = other.query(repo.Model).filter(repo.Model.id == snapshot.publish_id).first()
                assert native is not None
                expected = "draft" if snapshot.desired_stage == "draft" else "building"
                assert native.status == expected
            super().create(session, snapshot)
    repo._audit = ObservingPlugin()
    record = create(repository)
    repo.update_status(record.id, "building", "draft", audit_operator_id="editor")
    assert len(audits(repository)) == 2


def test_audit_commit_failure_keeps_native_publication(repository):
    repo, _ = repository
    original = repo._db.transactional_orm_session
    calls = 0
    @contextmanager
    def fail_audit_commit():
        nonlocal calls
        calls += 1
        with original() as session:
            yield session
            if calls == 1:
                raise RuntimeError("audit commit unavailable")
    repo._db.transactional_orm_session = fail_audit_commit
    record = create(repository)
    assert record.status == "draft"
    assert audits(repository) == []


@pytest.mark.parametrize("operation", ["status_ext", "ext_cas", "status_ext_cas"])
def test_autocommit_native_update_keeps_marker_and_uses_only_one_audit_transaction(repository, operation):
    repo, engine = repository
    class AutocommitDatabase(Database):
        def __init__(self):
            self.factory = sessionmaker(engine.execution_options(isolation_level="AUTOCOMMIT"), autoflush=False)
            self.audit_transactions = 0
        @contextmanager
        def transactional_orm_session(self):
            self.audit_transactions += 1
            factory = sessionmaker(engine.execution_options(isolation_level="SERIALIZABLE"), autoflush=False)
            with factory.begin() as session:
                yield session
    database = AutocommitDatabase()
    repo._db = database
    record = create(repository)
    if operation == "ext_cas":
        record = repo.update_status(record.id, "building", "draft", audit_operator_id="editor")
    before = database.audit_transactions
    replacement = {**(record.ext or {}), "native": "kept"}
    if operation == "status_ext":
        result = repo.update_status_with_ext(record.id, "building", replacement, "draft", audit_operator_id="editor")
    elif operation == "status_ext_cas":
        result = repo.compare_and_set_status_with_ext(
            publish_id=record.id, source_status="draft", target_status="building",
            expected_ext=record.ext, ext=replacement, audit_operator_id="editor")
    else:
        result = repo.compare_and_set_ext(publish_id=record.id, expected_ext=record.ext, ext=replacement)
    assert database.audit_transactions == before + 1
    persisted = repo.get_by_id(record.id)
    assert result.status == persisted.status == "building"
    assert persisted.ext["native"] == "kept"
    assert persisted.ext["_release_audit_change_id"] == audits(repository)[-1].change_id
    assert audits(repository)[-1].operator_id == "editor"
    # The next CAS must match the exact committed marker and native ext.
    done = repo.compare_and_set_status_with_ext(
        publish_id=record.id, source_status="building", target_status="failed",
        expected_ext=persisted.ext, ext=persisted.ext)
    assert done.status == "failed"
    assert audits(repository)[-1].finish_status == "failed"


@pytest.mark.parametrize("interleaving", ["before_cursor_execute", "after_cursor_execute"])
def test_autocommit_status_marker_is_atomic_and_keeps_concurrent_ext(repository, interleaving):
    from sqlalchemy import event
    repo, engine = repository
    class AutocommitDatabase(Database):
        def __init__(self):
            self.factory = sessionmaker(engine.execution_options(isolation_level="AUTOCOMMIT"), autoflush=False)
        @contextmanager
        def transactional_orm_session(self):
            factory = sessionmaker(engine.execution_options(isolation_level="SERIALIZABLE"), autoflush=False)
            with factory.begin() as session:
                yield session
    repo._db = AutocommitDatabase()
    record = create(repository)
    observed = []
    def concurrent_writer(connection, cursor, statement, parameters, context, executemany):
        if observed or not statement.startswith("UPDATE ac_bot_publish SET status="):
            return
        current = repo.get_by_id(record.id)
        observed.append(current.status)
        if interleaving == "after_cursor_execute":
            # Another connection sees status and change marker in the same write.
            assert current.status == "building"
            assert current.ext["_release_audit_change_id"]
        updated = repo.compare_and_set_ext(
            publish_id=record.id, expected_ext=current.ext,
            ext={**(current.ext or {}), "concurrent_native": "must survive"})
        assert updated is not None
    event.listen(engine, interleaving, concurrent_writer)
    try:
        result = repo.update_status(record.id, "building", "draft", audit_operator_id="editor")
    finally:
        event.remove(engine, interleaving, concurrent_writer)
    assert observed
    assert result.status == "building"
    assert result.ext["concurrent_native"] == "must survive"
    assert result.ext["_release_audit_change_id"] == audits(repository)[-1].change_id
    assert audits(repository)[-1].operator_id == "editor"
