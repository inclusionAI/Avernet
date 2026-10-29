"""Knowledge state follows observed deployment success, never admission status."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.bot_knowledge.repository.store import (
    BotKnowledgeRepository,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel,
    PublishOperationModel,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
    PublishProgressPollHandler,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.kernel.knowledge import BotKnowledgeScope
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding,
    BotModel,
    _online,
    _real_flow,
    _start_online,
    publication as publication_fixture,
)
from tests.community.repository.publishing.test_knowledge_short_transaction import (
    finish,
)

publication = publication_fixture


def space(db):
    with db.orm_session() as session:
        session.add(
            SpaceModel(
                id=9,
                space_code="personal-knowledge",
                space_type="PERSONAL",
                name="Personal",
                personal_owner_id="owner",
                env="dev",
                created_by="owner",
                updated_by="owner",
            )
        )


def selected(db, owner="owner"):
    return BotKnowledgeRepository(db).list_enabled(
        BotKnowledgeScope("service", owner, "dev"),
        selection_stage="online",
    )


def version(repo, previous, *, owner="owner", status=PublishStatus.DRAFT, **extra):
    return repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.pub",
            name="Service",
            owner_id=previous.owner_id,
            permission_owner=owner,
            status=status,
            version=previous.version + 1,
            env="dev",
            last_pub_id=previous.id,
            **extra,
        )
    )


def deploying(repo, record, workflow_id):
    online = _start_online(repo, record)
    return repo.update_status_with_ext(
        record.id,
        PublishStatus.ONLINE_PUB,
        {**online.ext, "publish": {"online": workflow_id}, "binding": {"online": 11}},
        PublishStatus.ONLINE_PUB,
    )


def poll(repo, publish_id):
    flow, queue = _real_flow(repo)
    flow.get_baas_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
    return PublishProgressPollHandler(flow=flow, task_queue_service=queue).handle(
        {"publish_id": publish_id},
    )


@pytest.mark.asyncio
async def test_unknown_survives_real_rollback_failure_and_redraft(publication):
    db, repo, current = publication
    space(db)
    legacy = repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.pub",
            name="Service",
            owner_id="owner",
            permission_owner="owner",
            status=PublishStatus.UPGRADED,
            version=0,
            env="dev",
            ext={"migration_path": "/legacy"},
        )
    )
    with db.orm_session() as session:
        session.query(BotPublishModel).filter_by(id=current.id).update(
            {"last_pub_id": legacy.id}
        )
        session.query(Binding).update({"avernet_tenant": "external"})
        session.query(BotModel).filter_by(id=1).update({"avernet_tenant": "external"})
    # Real default-tenant poll cannot infer the legacy tenant or clear its flags.
    online = repo.update_status_with_ext(
        current.id,
        PublishStatus.ONLINE_PUB,
        {"publish": {"online": 200}, "binding": {"online": 11}},
        PublishStatus.DRAFT,
    )
    with avernet_tenant_scope("external"), db.orm_session() as session:
        session.query(Binding).filter_by(kb_id="A").update({"online_enabled": True})
    poll(repo, online.id)
    unknown = repo.get_by_id(current.id).ext["knowledge_selection_result"]
    assert unknown["status"] == "unknown" and unknown["deployment_id"] == 200
    with avernet_tenant_scope("external"):
        assert _online(db) == {"A"}  # no tenant-guard bypass
        assert selected(db) == ()
    flow, _queue = _real_flow(repo)
    flow._publish_service._publish_flow_service_provider = lambda: flow
    flow.execute_rollback = AsyncMock(
        side_effect=RuntimeError("deployment submission failed")
    )
    with pytest.raises(RuntimeError, match="deployment submission failed"):
        await flow._publish_service.rollback_publish(current.id, operator="owner")
    assert repo.get_by_id(current.id).status == PublishStatus.DRAFT
    with avernet_tenant_scope("external"):
        assert selected(db) == ()
        rebuilt = repo.update_status(
            current.id, PublishStatus.BUILDING, PublishStatus.DRAFT
        )
        assert rebuilt.ext["knowledge_selection_result"] == unknown
        assert selected(db) == ()  # capture is not successful deployment


@pytest.mark.asyncio
async def test_rollback_admission_does_not_reactivate_old_unknown_result(publication):
    db, repo, old = publication
    space(db)
    old = repo.update_status_with_ext(
        old.id,
        PublishStatus.ONLINE_PUB,
        {
            "publish": {"online": 100},
            "binding": {"online": 11},
            "migration_path": "/old",
        },
        PublishStatus.DRAFT,
    )
    old = finish(repo, old)
    assert old.ext["knowledge_selection_result"]["status"] == "unknown"
    current = version(repo, old)
    current = finish(repo, deploying(repo, current, 200))
    repo.update_status(old.id, PublishStatus.UPGRADED, PublishStatus.SUCCESS)
    assert selected(db) == ("B",)
    flow, _queue = _real_flow(repo)
    flow._publish_service._publish_flow_service_provider = lambda: flow
    flow.execute_rollback = AsyncMock(
        side_effect=RuntimeError("deployment submission failed")
    )
    with pytest.raises(RuntimeError, match="deployment submission failed"):
        await flow._publish_service.rollback_publish(current.id, operator="owner")
    # Native admission flips current→DRAFT and old→SUCCESS, but the actual live
    # deployment is still workflow200 and its knowledge selection stays B.
    assert repo.get_by_id(current.id).status == PublishStatus.DRAFT
    assert repo.get_by_id(old.id).status == PublishStatus.SUCCESS
    assert selected(db) == ("B",)
    restored = repo.update_status_with_ext(
        old.id,
        PublishStatus.ONLINE_PUB,
        {**repo.get_by_id(old.id).ext, "publish": {"online": 300}},
        PublishStatus.SUCCESS,
    )
    finish(repo, restored)
    assert selected(db) == ()  # only actual target success activates UNKNOWN300


@pytest.mark.parametrize("owner_changed", [False, True])
def test_late_callback_across_uuid_or_owner_replacement_cannot_touch_new_flags(
    publication,
    owner_changed,
):
    db, repo, first = publication
    space(db)
    old = deploying(repo, first, 100)
    owner = "new-owner" if owner_changed else "owner"
    with db.orm_session() as session:
        if owner_changed:
            session.query(BotModel).filter_by(id=1).update({"owner_id": owner})
        session.query(Binding).update({"enabled": False})
        session.query(Binding).filter_by(kb_id="A").update({"enabled": True})
    current = version(repo, first, owner=owner)
    current = deploying(repo, current, 200)
    with db.orm_session() as session:
        session.add_all(
            [
                PublishOperationModel(
                    publish_id=row.id,
                    stage="online",
                    operation_kind="upgrade",
                    attempt=1,
                    state="completed",
                    request_id=f"release-{workflow}",
                    bot_uuid=uuid,
                    baas_publish_id=workflow,
                    env="dev",
                )
                for row, workflow, uuid in (
                    (old, 100, "old-device"),
                    (current, 200, "replacement-device"),
                )
            ]
        )
    poll(repo, current.id)
    assert selected(db, owner) == ("A",)
    poll(repo, old.id)  # observed200 fences late100 even after Owner/UUID change
    late = repo.get_by_id(old.id)
    # The late native ledger completes; only its knowledge result stays fenced.
    assert late.status == PublishStatus.SUCCESS
    result = late.ext["knowledge_selection_result"]
    assert result["status"] == "unknown"
    assert result["deployment_id"] == 100
    assert _online(db) == {"A"}
    assert selected(db, owner) == ("A",)


def test_unorderable_observed_workflow_completes_with_unknown_without_effects(
    publication,
):
    db, repo, record = publication
    space(db)
    with db.orm_session() as session:
        session.query(Binding).update({"enabled": False})
        session.query(Binding).filter_by(kb_id="A").update({"enabled": True})
    current = deploying(repo, record, 200)
    finished = repo.compare_and_set_status_with_ext(
        publish_id=current.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=current.ext,
        ext=dict(current.ext),
        observed_workflow_id="not-an-int",
    )
    assert finished.status == PublishStatus.SUCCESS
    result = finished.ext["knowledge_selection_result"]
    # Unorderable observed workflow: explicit unknown, zero knowledge effects.
    assert result["status"] == "unknown"
    assert result["deployment_id"] == 0
    assert selected(db) == ()


@pytest.mark.parametrize("stale_marker", [False, True])
def test_restart_completion_uses_workflow_actually_observed_not_release_slot(
    publication,
    stale_marker,
):
    from agentclaw.community.core.repository.implementations.publishing.publish_operation import (
        OrmPublishOperationRepository,
    )

    db, repo, previous = publication
    space(db)
    previous = finish(repo, deploying(repo, previous, 200))
    with db.orm_session() as session:
        session.query(Binding).update({"enabled": False})
        session.query(Binding).filter_by(kb_id="A").update({"enabled": True})
    current = version(repo, previous)
    current = _start_online(repo, current)  # native original release slot remains77
    restart = {"restarting": True}
    if stale_marker:
        restart["online"] = 299  # best-effort dual-write can be stale/missing
    repo.update_status_with_ext(
        current.id,
        PublishStatus.ONLINE_PUB,
        {
            **current.ext,
            "retry": True,
            "source_status": "online_pub",
            "restart": restart,
            "binding": {"online": 11},
        },
        PublishStatus.ONLINE_PUB,
    )
    ledger = OrmPublishOperationRepository(db)
    ledger.insert(
        dict(
            publish_id=current.id,
            stage="online",
            operation_kind="restart",
            attempt=1,
            state="completed",
            request_id="retry-restart-300",
            bot_uuid="device",
            baas_publish_id=300,
            env="dev",
        )
    )
    flow, queue = _real_flow(repo)
    flow._publish_operation_repo = ledger
    flow.get_baas_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
    PublishProgressPollHandler(flow=flow, task_queue_service=queue).handle(
        {"publish_id": current.id}
    )
    flow.get_baas_publish_progress.assert_called_once_with(baas_publish_id=300)
    completed = repo.get_by_id(current.id)
    assert completed.status == PublishStatus.SUCCESS
    assert completed.ext["publish"]["online"] == 77  # no native-history rewrite
    assert completed.ext["knowledge_selection_result"]["deployment_id"] == 300
    assert selected(db) == ("A",)


def test_native_stable_entity_owner_survives_current_owner_handoff(publication):
    db, repo, previous = publication
    space(db)
    old = deploying(repo, previous, 100)
    with db.orm_session() as session:
        session.query(BotModel).filter_by(id=1).update({"owner_id": "new-owner"})
        source = session.get(BotModel, 1).to_dict()
        session.query(Binding).update({"enabled": False})
        session.query(Binding).filter_by(kb_id="A").update({"enabled": True})
    flow, _queue = _real_flow(repo)
    flow._publish_service._bot_repo.get_by_pk.return_value = source
    current = flow._publish_service.create_publish(
        source_bot_pk=1,
        source_bot_id="service",
        publish_bot_id="service.pub",
        name="Service",
        owner_id="new-owner",
        permission_owner="owner",
        version=2,
        last_pub_id=old.id,
    )
    # This is the actual native create contract, not an invented fixture owner.
    assert current.owner_id == source["entity_id"] == "owner"
    current = deploying(repo, current, 200)
    snapshot = current.ext["knowledge_selection_snapshot"]
    assert snapshot["owner_id"] == "new-owner"
    assert snapshot["source_entity_id"] == "owner"
    poll(repo, current.id)
    assert (
        repo.get_by_id(current.id).ext["knowledge_selection_result"]["owner_id"]
        == "new-owner"
    )
    poll(repo, old.id)
    late = repo.get_by_id(old.id)
    # The late native ledger completes; the winning deployment keeps its flags.
    assert late.status == PublishStatus.SUCCESS
    assert late.ext["knowledge_selection_result"]["status"] == "unknown"
    assert _online(db) == {"A"}
    assert selected(db, "new-owner") == ("A",)
