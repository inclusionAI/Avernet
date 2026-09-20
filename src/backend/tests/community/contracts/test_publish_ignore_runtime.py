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


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
@pytest.mark.parametrize("engine_success", [True, False])
async def test_query_consumer_calls_pinned_runtime(world, stage, engine_success):
    from agentclaw.community.api.publish_ignore_service import PublishIgnoreQuery
    from tests.community.factories.publish_ignore import (
        seed_publish_ignore_query, assert_ignore_query_called,
    )
    seed_publish_ignore_query(world, stage=stage, engine_success=engine_success)
    result = await world.get(PublishIgnoreServiceProtocol).query(
        PublishIgnoreQuery("ignore-bot", "ignore_owner", stage, "contract-query"),
        "ignore_owner", is_admin=False,
    )
    assert result["success"] is engine_success
    if engine_success:
        assert result["results"][0]["paths"] == ["workspace/bin"]
    else:
        assert "paths" not in result["results"][0]
    assert_ignore_query_called(None, world)
