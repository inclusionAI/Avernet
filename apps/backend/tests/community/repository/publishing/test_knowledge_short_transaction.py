"""Short local publication bookkeeping, not a second publication workflow."""

import copy
import json
from contextlib import contextmanager

import pytest
from tests.community.core.bot_knowledge.summary_fixtures import seed_online
from sqlalchemy import event

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel,
    PublishStatus,
)
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding,
    BotModel,
    _online,
    _real_flow,
    _start_online,
    publication as publication_fixture,
)

publication = publication_fixture


def finish(repo, online):
    return repo.compare_and_set_status_with_ext(
        publish_id=online.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=online.ext,
        ext=copy.deepcopy(online.ext),
        observed_workflow_id=((online.ext or {}).get("publish") or {}).get("online", 77),
    )


@pytest.mark.parametrize("change", ["missing_binding", "owner", "corrupt", "missing"])
def test_knowledge_business_states_are_unknown_not_native_failures(publication, change):
    db, repo, record = publication
    seed_online(db, record, ['A'])
    online = _start_online(repo, record)
    with db.orm_session() as session:
        session.query(Binding).filter_by(kb_id="A").update({"online_enabled": True})
        if change == "missing_binding":
            session.query(Binding).filter_by(kb_id="B").delete()
        elif change == "owner":
            session.query(BotModel).filter_by(id=1).update({"owner_id": "new-owner"})
        else:
            ext = dict(online.ext)
            if change == "missing":
                ext.pop("knowledge_selection_snapshot")
            else:
                ext["knowledge_selection_snapshot"] = ["corrupt-history"]
            session.query(BotPublishModel).filter_by(id=record.id).update(
                {"ext": json.dumps(ext)}
            )
    online = repo.get_by_id(record.id)
    completed = finish(repo, online)
    assert completed.status == PublishStatus.SUCCESS
    assert completed.ext["knowledge_selection_result"]["status"] == "unknown"
    assert _online(db) == set()


def test_background_completion_restores_snapshot_tenant_without_guard_bypass(
    publication,
):
    db, repo, record = publication
    # This fixture setup changes persisted ownership, not the production path.
    with db.orm_session() as session:
        session.query(Binding).update({"avernet_tenant": "external"})
        session.query(BotModel).filter_by(id=1).update({"avernet_tenant": "external"})
    with avernet_tenant_scope("external"):
        online = _start_online(repo, record)
        assert (
            online.ext["knowledge_selection_snapshot"]["avernet_tenant"] == "external"
        )
    assert get_current_avernet_tenant() == "teamclaw"
    completed = finish(repo, online)
    assert completed.ext["knowledge_selection_result"]["status"] == "applied"
    assert get_current_avernet_tenant() == "teamclaw"
    assert _online(db) == set()
    with avernet_tenant_scope("external"):
        assert _online(db) == {"B"}


@pytest.mark.parametrize(
    "method", ["status_ext", "ext_cas", "status_cas", "built", "online_cas"]
)
def test_changed_artifact_put_happens_before_any_explicit_transaction(
    publication, method
):
    db, repo, record = publication
    original = db.transactional_orm_session
    entered = {"value": False}

    @contextmanager
    def tracked():
        entered["value"] = True
        try:
            with original() as session:
                yield session
        finally:
            entered["value"] = False

    db.transactional_orm_session = tracked
    objects = {}
    storage = repo._offload._oss

    def put(key, body):
        assert not entered["value"], "remote PUT under knowledge transaction"
        objects[key] = body
        return True

    storage.put_object.side_effect = put
    storage.get_object.side_effect = lambda key: objects.get(key)
    if method == "online_cas":
        record = _start_online(repo, record)
    ext = {**record.ext, "config_artifact": {"data": "x" * 62000}}
    if method == "status_ext":
        result = repo.update_status_with_ext(
            record.id, PublishStatus.FAILED, ext, PublishStatus.DRAFT
        )
    elif method == "ext_cas":
        result = repo.compare_and_set_ext(
            publish_id=record.id, expected_ext=record.ext, ext=ext
        )
    else:
        args = dict(
            publish_id=record.id,
            source_status=PublishStatus.ONLINE_PUB
            if method == "online_cas"
            else PublishStatus.DRAFT,
            target_status=PublishStatus.SUCCESS
            if method == "online_cas"
            else PublishStatus.FAILED,
            expected_ext=record.ext,
            ext=ext,
        )
        if method == "built":
            result = repo.compare_and_set_built_with_ext(
                **args, center_skill_uuids=(), env="dev"
            )
        else:
            if method == "online_cas":
                args["observed_workflow_id"] = record.ext["publish"]["online"]
            result = repo.compare_and_set_status_with_ext(**args)
    assert result.ext["config_artifact"] == ext["config_artifact"]
    storage.put_object.assert_called_once()


@pytest.mark.parametrize("stale", ["missing", "status", "ext"])
def test_known_stale_artifact_cas_does_not_upload(publication, stale):
    _db, repo, record = publication
    args = dict(
        publish_id=record.id,
        source_status=PublishStatus.DRAFT,
        target_status=PublishStatus.FAILED,
        expected_ext=record.ext,
        ext={"config_artifact": {"data": "x" * 62000}},
    )
    if stale == "missing":
        args["publish_id"] = 999
    elif stale == "status":
        args["source_status"] = PublishStatus.BUILDING
    else:
        args["expected_ext"] = {"stale": True}
    assert repo.compare_and_set_status_with_ext(**args) is None
    repo._offload._oss.put_object.assert_not_called()


def test_native_schema_matches_original_pr_without_journal_columns():
    assert not any(
        col.name.startswith("knowledge_") for col in BotPublishModel.__table__.columns
    )
    assert not any(
        "knowledge" in index.name for index in BotPublishModel.__table__.indexes
    )
    # Only the original knowledge-owned binding table is used.
    assert not any(
        "knowledge_publication" in name for name in BotPublishModel.metadata.tables
    )


def test_real_success_retry_after_db_failure_does_not_deploy_again(publication):
    from unittest.mock import MagicMock
    from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
        PublishProgressPollHandler,
    )
    from agentclaw.community.core.task_queue.types import Complete

    db, repo, record = publication
    online = _start_online(repo, record)
    online = repo.update_status_with_ext(
        record.id,
        PublishStatus.ONLINE_PUB,
        {**online.ext, "binding": {"online": 11}},
        PublishStatus.ONLINE_PUB,
    )
    flow, _queue = _real_flow(repo)
    engine = db.factory.kw["bind"]

    def fail(_conn, _cursor, sql, *_args):
        if sql.startswith("UPDATE ac_bot_knowledge_binding"):
            raise RuntimeError("simulated transient DB error")

    flow.get_baas_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
    handler = PublishProgressPollHandler(flow=flow, task_queue_service=MagicMock())

    def complete():
        return handler.handle({"publish_id": record.id})

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="transient DB error"):
            complete()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert repo.get_by_id(record.id).status == PublishStatus.ONLINE_PUB
    assert isinstance(complete(), Complete)
    assert isinstance(complete(), Complete)  # repeated terminal delivery is a no-op
    assert flow.get_baas_publish_progress.call_count == 2
    assert _online(db) == {"B"}
    # The callback has only observed an already successful external deployment;
    # none of the native deploy entry points are invoked by its DB retry.
    assert all(
        call[0].startswith("resolve_container_provider")
        for call in flow._baas_service.mock_calls
    ), "only pure provider resolution is allowed; no BaaS deployment mutator"


def test_capture_business_error_is_unknown_and_keeps_attempt_fence(
    publication, monkeypatch
):
    from agentclaw.community.core.repository.implementations.publishing import (
        knowledge_selection,
    )

    _db, repo, record = publication

    def unavailable(*_args):
        raise KnowledgeError("KNOWLEDGE_PUBLISH_SOURCE_MISMATCH")

    monkeypatch.setattr(knowledge_selection, "_locked_source", unavailable)
    built = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    snapshot = built.ext["knowledge_selection_snapshot"]
    assert snapshot["status"] == "unknown"
    assert len(snapshot["generation"]) == 32
    assert "enabled_kb_ids" not in snapshot
    assert built.status == PublishStatus.BUILDING


def test_normal_ext_over_60kb_is_not_rejected_by_a_knowledge_only_limit(publication):
    _db, repo, record = publication
    repo.update_status_with_ext(
        record.id,
        PublishStatus.DRAFT,
        {"data": "中" * 19980},
        PublishStatus.DRAFT,
    )
    built = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    assert built.status == PublishStatus.BUILDING
    assert built.ext["knowledge_selection_snapshot"]["enabled_kb_ids"] == ["B"]


def test_mutable_snapshot_and_completion_reads_are_current_locking_reads(publication):
    from sqlalchemy.dialects import mysql
    from sqlalchemy.orm import Session

    _db, repo, record = publication
    reads = []

    def inspect(orm):
        if orm.is_select:
            reads.append(str(orm.statement.compile(dialect=mysql.dialect())))

    event.listen(Session, "do_orm_execute", inspect)
    try:
        online = _start_online(repo, record)
        finish(repo, online)
    finally:
        event.remove(Session, "do_orm_execute", inspect)
    # A plain consistent read after waiting on a Bot row would retain the
    # pre-lock MySQL REPEATABLE READ snapshot. Locking reads see the editor or
    # deployment that committed while we waited; SQLite concurrency alone
    # cannot establish that property.
    mutable_reads = [
        sql
        for sql in reads
        if "FROM ac_bot_knowledge_binding" in sql
        or "FROM ac_bot_common_config" in sql
    ]
    assert mutable_reads
    assert all("FOR UPDATE" in sql for sql in mutable_reads)


def test_stale_rollback_pair_does_not_put_either_artifact(publication):
    _db, repo, record = publication
    assert repo.rollback_flip(
        demoted_publish_id=record.id,
        demoted_ext={**record.ext, "config_artifact": {"data": "x" * 62000}},
        demoted_from_status=PublishStatus.DRAFT,
        demoted_to_status=PublishStatus.UPGRADED,
        restored_publish_id=999,
        restored_ext={},
        restored_from_status=PublishStatus.UPGRADED,
        restored_to_status=PublishStatus.SUCCESS,
    ) == (False, False)
    repo._offload._oss.put_object.assert_not_called()


def test_delete_racing_pre_staged_put_does_not_leave_unowned_objects(publication):
    _db, repo, record = publication
    storage = repo._offload._oss
    objects = {}
    storage.list_objects.side_effect = lambda prefix: [
        key for key in objects if key.startswith(prefix)
    ]
    storage.delete_object.side_effect = lambda key: objects.pop(key, None)

    def put(key, body):
        # Native delete cleans before the upload becomes visible.
        assert repo.delete(record.id)
        objects[key] = body
        return True

    storage.put_object.side_effect = put
    assert (
        repo.compare_and_set_status_with_ext(
            publish_id=record.id,
            source_status=PublishStatus.DRAFT,
            target_status=PublishStatus.FAILED,
            expected_ext=record.ext,
            ext={"config_artifact": {"data": "x" * 62000}},
        )
        is None
    )
    assert not objects
    assert repo.get_by_id(record.id) is None
