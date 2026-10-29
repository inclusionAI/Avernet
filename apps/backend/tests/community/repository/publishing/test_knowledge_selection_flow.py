"""Real flow callbacks and artifact CAS preserve the admitted knowledge generation."""

import pytest
from tests.community.core.bot_knowledge.summary_fixtures import seed_online
from agentclaw.community.core.repository.implementations.publishing.bot_publish import (
    BotPublishRepository,
)
from agentclaw.community.core.service_bot.repository.config_artifact_offload import (
    ConfigArtifactOffloader,
)
from agentclaw.community.kernel.knowledge import KnowledgePublicationPolicy

from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding,
    PublishStatus,
    _online,
    _real_flow,
    _start_online,
)
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    publication as publication_fixture,
)

publication = publication_fixture


@pytest.mark.asyncio
async def test_real_service_process_freezes_and_observed_online_success_promotes(
    publication,
):
    import copy

    from agentclaw.community.core.service_bot.types import PublishStage

    db, repo, record = publication
    seed_online(db, record, ['A'])
    flow, queue = _real_flow(repo)
    result = await flow.process(record.id, "owner")
    assert result.status == PublishStatus.BUILDING
    frozen = repo.get_by_id(record.id)
    assert frozen.ext["knowledge_selection_snapshot"]["enabled_kb_ids"] == ["B"]
    assert queue.enqueue.call_args.args[0] == "service_bot.publish.verify_flow"
    online = repo.update_status_with_ext(
        record.id,
        PublishStatus.ONLINE_PUB,
        {**frozen.ext, "publish": {"online": 77}, "binding": {"online": 11}},
        source_status=PublishStatus.BUILDING,
    )
    with db.orm_session() as session:
        session.query(Binding).filter(Binding.kb_id == "A").update(
            {Binding.online_enabled: True}
        )
    result = flow._handle_sync_success(
        publish_id=record.id,
        publish_record=online,
        stage=PublishStage.ONLINE,
        ext=copy.deepcopy(online.ext),
        baas_publish_id=77,
        progress={"status": "SUCCESS"},
    )
    assert result.status == PublishStatus.SUCCESS
    assert repo.get_by_id(record.id).status == PublishStatus.SUCCESS
    assert _online(db) == {"B"}


def test_real_service_success_rolls_back_status_when_production_write_fails(
    publication,
):
    import copy

    from agentclaw.community.core.service_bot.types import PublishStage
    from sqlalchemy import event

    db, repo, record = publication
    flow, _ = _real_flow(repo)
    online = _start_online(repo, record)
    with db.orm_session() as session:
        session.query(Binding).filter(Binding.kb_id == "A").update(
            {Binding.online_enabled: True}
        )
    engine = db.factory.kw["bind"]

    def fail_binding_write(
        _connection, _cursor, statement, _parameters, _context, _many
    ):
        if statement.startswith("UPDATE ac_bot_knowledge_binding"):
            raise RuntimeError("injected binding persistence failure")

    event.listen(engine, "before_cursor_execute", fail_binding_write)
    try:
        with pytest.raises(RuntimeError, match="injected binding persistence failure"):
            flow._handle_sync_success(
                publish_id=record.id,
                publish_record=online,
                stage=PublishStage.ONLINE,
                ext=copy.deepcopy(online.ext),
                baas_publish_id=77,
                progress={"status": "SUCCESS"},
            )
    finally:
        event.remove(engine, "before_cursor_execute", fail_binding_write)
    assert repo.get_by_id(record.id).status == PublishStatus.ONLINE_PUB
    assert _online(db) == {"A"}


@pytest.mark.asyncio
async def test_old_build_result_cannot_join_a_new_frozen_generation(publication):
    from unittest.mock import AsyncMock

    from agentclaw.community.core.service_bot.services.publish_flow.build_stage import (
        BuildArtifactOnlyResult,
    )
    from agentclaw.community.core.service_bot.services.publish_flow.errors import (
        PublishFlowServiceError,
    )

    _db, repo, record = publication
    old = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    repo.update_status(record.id, PublishStatus.DRAFT, PublishStatus.BUILDING)
    current = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    flow, _ = _real_flow(repo)
    flow._build_stage_runner.build_artifact_only = AsyncMock(
        return_value=BuildArtifactOnlyResult(
            migration_path="/old-artifact",
            config_artifact=None,
            docker_image=None,
            center_skill_uuids=(),
            artifact_ext={"migration_path": "/old-artifact"},
            employee_snapshot=None,
        )
    )
    with pytest.raises(PublishFlowServiceError, match="Publication attempt superseded"):
        await flow.execute_build_phase(old, "owner")
    assert repo.get_by_id(record.id).status == PublishStatus.BUILDING
    assert repo.get_by_id(record.id).ext == current.ext


def test_frozen_snapshot_keeps_inline_artifact_ext_canonical_for_next_cas(publication):
    _db, repo, record = publication
    repo.update_status_with_ext(
        record.id,
        PublishStatus.DRAFT,
        {"config_artifact": {"engine_type": "teclaw"}},
        source_status=PublishStatus.DRAFT,
    )
    frozen = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    changed = repo.compare_and_set_ext(
        publish_id=record.id,
        expected_ext=frozen.ext,
        ext={**frozen.ext, "progress": "building"},
    )
    assert changed is not None
    assert changed.ext["config_artifact"] == {"engine_type": "teclaw"}
    assert (
        changed.ext["knowledge_selection_snapshot"]
        == frozen.ext["knowledge_selection_snapshot"]
    )


def test_frozen_snapshot_preserves_offloaded_artifact_and_exact_ext_cas(publication):
    class Storage:
        def __init__(self):
            self.objects = {}

        def put_object(self, key, body):
            self.objects[key] = body
            return True

        def get_object(self, key):
            return self.objects.get(key)

    db, _repo, record = publication
    storage = Storage()
    repo = BotPublishRepository(
        db,
        offload=ConfigArtifactOffloader(storage),
        knowledge_policy=KnowledgePublicationPolicy(enabled=True),
    )
    artifact = {"large": "x" * 65000}
    repo.update_status_with_ext(
        record.id,
        PublishStatus.DRAFT,
        {"config_artifact": artifact},
        source_status=PublishStatus.DRAFT,
    )
    assert len(storage.objects) == 1
    frozen = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    changed = repo.compare_and_set_ext(
        publish_id=record.id,
        expected_ext=frozen.ext,
        ext={**frozen.ext, "progress": "building"},
    )
    assert changed is not None
    assert changed.ext["config_artifact"] == artifact
    assert (
        changed.ext["knowledge_selection_snapshot"]
        == frozen.ext["knowledge_selection_snapshot"]
    )


def test_snapshot_cannot_be_replaced_by_generic_ext_writer(publication):
    from agentclaw.community.kernel.knowledge import KnowledgeError

    _db, repo, record = publication
    frozen = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    tampered = {
        **frozen.ext,
        "knowledge_selection_snapshot": {
            **frozen.ext["knowledge_selection_snapshot"],
            "enabled_kb_ids": ["A"],
        },
    }
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_SNAPSHOT_READ_ONLY"):
        repo.compare_and_set_ext(
            publish_id=record.id, expected_ext=frozen.ext, ext=tampered
        )
    assert repo.get_by_id(record.id).ext == frozen.ext
