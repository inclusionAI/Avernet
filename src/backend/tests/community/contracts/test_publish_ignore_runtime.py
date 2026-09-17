"""Composition and fail-closed service contract for publish-ignore."""

import pytest
from agentclaw.community.api.publish_ignore_service import (
    PublishIgnoreCommand,
    PublishIgnoreError,
    PublishIgnoreServiceProtocol,
)
from agentclaw.community.core.service_bot.services.publish_ignore_service import (
    PublishIgnoreService,
)
from agentclaw.community.plugins.community.publish_ignore_runtime import (
    HttpPublishIgnoreRuntime,
)


@pytest.mark.asyncio
async def test_composition_and_anonymous_denial(world):
    service = world.get(PublishIgnoreServiceProtocol)
    assert isinstance(service, PublishIgnoreService)
    assert isinstance(service.runtime, HttpPublishIgnoreRuntime)
    command = PublishIgnoreCommand(
        "missing", "entity", "online", "add", "cache", "request"
    )
    with pytest.raises(PublishIgnoreError, match="permission_denied"):
        await service.change(command, "anonymous", is_admin=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_success", [True, False])
@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
async def test_consumer_calls_signed_pinned_runtime(world, engine_success, stage):
    from tests.community.factories.publish_ignore import (
        seed_publish_ignore,
        assert_ignore_engine_called,
    )

    seed_publish_ignore(world, engine_success=engine_success, stage=stage)
    service = world.get(PublishIgnoreServiceProtocol)
    command = PublishIgnoreCommand(
        "ignore-bot", "ignore_owner", stage, "add", "cache", "contract"
    )
    result = await service.change(command, "ignore_owner", is_admin=False)
    assert result["success"] is engine_success
    assert_ignore_engine_called(None, world)
