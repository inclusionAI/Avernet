"""Publication writes produce durable audit events in their own transaction."""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.publishing.bot_publish import BotPublishRepository
from agentclaw.community.core.service_bot.repository.config_artifact_offload import ConfigArtifactOffloader
from agentclaw.community.kernel.service_bot_release_audit import ReleaseAuditEvent
from agentclaw.community.plugin_api.models import Base, BotModel
from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditEventModel


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


@pytest.fixture
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    Base.metadata.create_all(engine)
    database = Database(engine)
    with database.orm_session() as session:
        session.add(BotModel(id=1, bot_id="bot-a", bot_name="A", entity_id="stable-entity",
                             entity_type="staff", creator_id="original-creator", owner_id="new-owner",
                             bot_type="service", active_engine="teclaw", status="ACTIVE", env="dev"))
    return BotPublishRepository(database, ConfigArtifactOffloader(None)), engine


def create(repository):
    return repository[0].insert(dict(source_bot_pk=1, source_bot_id="bot-a", publish_bot_id="bot-a",
                                    name="A", owner_id="stable-entity", permission_owner="owner",
                                    version=1, env="dev", status="draft", audit_operator_id="creator-delegate"))


def events(repository):
    with repository[0]._db.orm_session() as session:
        return [ReleaseAuditEvent.from_json(row.payload_json)
                for row in session.query(ReleaseAuditEventModel).order_by(ReleaseAuditEventModel.id)]


def test_draft_creation_records_original_creator_and_actor(repository):
    record = create(repository)
    produced = events(repository)
    assert len(produced) == 1
    snapshot = produced[0].snapshot
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
    assert started is not None
    before = repo.get_by_id(record.id).ext
    repo.compare_and_set_status_with_ext(publish_id=record.id, source_status="building",
                                        target_status="validate_pub", expected_ext=before,
                                        ext={**before, "publish": {"verify": 101}})
    before = repo.get_by_id(record.id).ext
    with repo._db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 1).update({"creator_id": "later-change"})
    repo.compare_and_set_status_with_ext(publish_id=record.id, source_status="validate_pub",
                                        target_status="validating", expected_ext=before, ext=before)
    produced = events(repository)
    assert [value.revision for value in produced] == [2, 1, 2]
    submission, terminal = produced[-2:]
    assert submission.change_id == terminal.change_id
    assert terminal.snapshot.creator_id == "original-creator"
    assert terminal.snapshot.operator_id == "submitter"
    assert terminal.snapshot.finish_status == "succeeded"
    assert terminal.snapshot.final_publish_status == "validating"
    assert terminal.snapshot.submitted_at == submission.snapshot.submitted_at


def test_build_failure_and_retry_create_separate_changes(repository):
    repo, _ = repository
    record = create(repository)
    started = repo.update_status(record.id, "building", "draft", audit_operator_id="submitter")
    repo.compare_and_set_status_with_ext(publish_id=record.id, source_status="building",
                                        target_status="failed", expected_ext=started.ext, ext=started.ext)
    failed = events(repository)[-1]
    assert failed.snapshot.finish_status == "failed"
    retried = repo.update_status(record.id, "building", "failed", audit_operator_id="retry-actor")
    retry_event = events(repository)[-1]
    assert retry_event.change_id != failed.change_id
    assert retry_event.snapshot.operator_id == "retry-actor"
    # A late response from the first attempt cannot match the new ext fence.
    assert repo.compare_and_set_status_with_ext(
        publish_id=record.id, source_status="building", target_status="failed",
        expected_ext=started.ext, ext=started.ext,
    ) is None
    assert repo.get_by_id(record.id).status == "building"
    assert events(repository)[-1].change_id == retry_event.change_id
    assert retried.ext != started.ext


def test_online_submission_and_completion_keep_environment(repository):
    repo, _ = repository
    record = create(repository)
    repo.update_status(record.id, "validating", "draft")
    started = repo.update_status(record.id, "online_pub", "validating", audit_operator_id="deployer")
    repo.compare_and_set_status_with_ext(publish_id=record.id, source_status="online_pub",
                                        target_status="success", expected_ext=started.ext, ext=started.ext)
    terminal = events(repository)[-1].snapshot
    assert terminal.desired_stage == "online"
    assert terminal.env == "dev"
    assert terminal.finish_status == "succeeded"
    assert terminal.final_publish_status == "success"


def test_rejected_status_update_emits_no_event(repository):
    repo, _ = repository
    record = create(repository)
    assert repo.update_status(record.id, "building", "failed", audit_operator_id="submitter") is None
    assert len(events(repository)) == 1


def test_event_insert_failure_rolls_back_publication_status(repository):
    from sqlalchemy import event
    repo, _ = repository
    record = create(repository)

    def fail_insert(*args):
        raise RuntimeError("outbox unavailable")

    event.listen(ReleaseAuditEventModel, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="outbox unavailable"):
            repo.update_status(record.id, "building", "draft", audit_operator_id="submitter")
    finally:
        event.remove(ReleaseAuditEventModel, "before_insert", fail_insert)
    assert repo.get_by_id(record.id).status == "draft"
    assert len(events(repository)) == 1


def test_unattributed_legacy_writes_do_not_invent_actor(repository):
    repo, _ = repository
    record = create(repository)
    repo.update_status(record.id, "building", "draft")
    assert len(events(repository)) == 1


def test_retry_to_stable_success_waits_for_restart_confirmation(repository):
    repo, _ = repository
    record = create(repository)
    repo.update_status(record.id, "failed", "draft")
    started = repo.update_status_with_ext(record.id, "success", {"retry": True}, "failed",
                                          audit_operator_id="retry-actor")
    assert events(repository)[-1].revision == 1
    cid = events(repository)[-1].change_id
    pending = repo.compare_and_set_ext(publish_id=record.id, expected_ext=started.ext,
                                      ext={**started.ext, "restart": {"restarting": True}})
    assert events(repository)[-1].revision == 1
    complete_ext = {**pending.ext, "restart": {}, "_release_audit_restart_completed": cid}
    repo.compare_and_set_ext(publish_id=record.id, expected_ext=pending.ext, ext=complete_ext)
    assert events(repository)[-1].revision == 2
    assert events(repository)[-1].change_id == cid
    assert events(repository)[-1].snapshot.finish_status == "succeeded"


def test_delivery_scan_does_not_starve_events_behind_failed_batch(repository):
    from dataclasses import replace
    from unittest.mock import MagicMock
    from agentclaw.community.core.service_bot.services.release_audit_delivery import ReleaseAuditDelivery

    create(repository)
    snapshot = events(repository)[0].snapshot
    with repository[0]._db.orm_session() as session:
        session.query(ReleaseAuditEventModel).update({"attempts": 10})
        for index in range(101):
            value = ReleaseAuditEvent(replace(snapshot, change_id=f"change-{index}"))
            session.add(ReleaseAuditEventModel(
                event_id=value.event_id, change_id=value.change_id, revision=value.revision,
                publish_id=snapshot.publish_id, env=snapshot.env, avernet_tenant=snapshot.tenant_id,
                payload_json=value.to_json(), attempts=10 if index < 100 else 0,
            ))
    selected = ReleaseAuditDelivery(repository[0]._db, MagicMock()).pending_event_ids()
    assert "change-100:2" in selected


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
    assert events(repository)[-1].snapshot.operator_id == "second"
    assert events(repository)[-1].revision == 1
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
    terminal = events(repository)[-1].snapshot
    assert terminal.revision == 2
    assert terminal.finish_status == "failed"
    assert terminal.final_publish_status == "success"
    assert repo.get_by_id(record.id).status == "success"
