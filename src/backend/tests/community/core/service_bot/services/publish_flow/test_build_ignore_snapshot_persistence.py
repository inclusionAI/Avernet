"""Build artifact snapshot persistence through the real publication repository."""

import pytest
from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401


@pytest.mark.asyncio
async def test_build_stage_persists_rule_snapshot_without_losing_existing_ext(world):  # noqa: F811 - imported pytest fixture
    from unittest.mock import Mock
    from agentclaw.community.core.repository.protocols.publishing import (
        BotPublishRepositoryProtocol,
    )
    from agentclaw.community.core.service_bot.services.bot_publish_service import (
        BotPublishService,
    )
    from agentclaw.community.core.service_bot.services.publish_flow.ext_state import (
        PublishExtState,
    )
    from agentclaw.community.core.service_bot.repository.models import PublishStatus
    from tests.community.core.service_bot.services.publish_flow.test_build_artifact_only import (
        _runner, _SimpleProducer,
    )

    repository = world.get(BotPublishRepositoryProtocol)
    record = repository.insert({
        "source_bot_pk": 11, "source_bot_id": "b1",
        "publish_bot_id": "published-b1", "name": "Snapshot Bot",
        "owner_id": "u1", "permission_owner": "u1", "version": 1,
        "env": "dev", "status": PublishStatus.BUILDING.value,
        "ext": {"existing_setting": "retained", "binding": {"draft": 123}},
    })
    snapshot = {"engine_type": "openclaw", "paths": ["workspace/bin"], "revision": 7}
    runner, _ = _runner(_SimpleProducer(artifact_ext={
        "migration_path": "/snapshot/1", "publish_ignore": snapshot,
    }))
    runner._ext_state = PublishExtState(world.get(BotPublishService), Mock())
    result = await runner.build(record, "u1")
    assert result.status == PublishStatus.BUILT
    stored = repository.get_by_id(record.id)
    assert stored.status == PublishStatus.BUILT.value
    assert stored.ext["publish_ignore"] == snapshot
    assert stored.ext["migration_path"] == "/snapshot/1"
    assert stored.ext["existing_setting"] == "retained"
    assert stored.ext["binding"] == {"draft": 123}
