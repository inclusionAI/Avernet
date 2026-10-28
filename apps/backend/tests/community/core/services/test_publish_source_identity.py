"""Published resource coordinates come from the source Bot, not owner aliases."""

from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.service_bot.router_publish import get_publish_engine_config
from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.errors import BotAddressConflictError, BotAddressNotFoundError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.services.engine_config import EngineConfigService
from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401


def _bot(world, *, bot_id="published-source", entity="staff_u1", owner="new-owner", entity_type="proj", engine="openclaw"):
    return world.get(BotRepository).insert({
        "bot_id": bot_id, "bot_name": "Publish source", "owner_id": owner,
        "entity_id": entity, "entity_type": entity_type, "creator_id": "u1",
        "bot_type": "service" if bot_id != "default" else "personal",
        "active_engine": engine, "status": "ACTIVE",
    })


def _record(bot, locator, **changes):
    values = {
        "id": 17, "source_bot_pk": bot["id"], "source_bot_id": bot["bot_id"],
        "publish_bot_id": "published-version", "name": "Published version",
        "owner_id": locator, "permission_owner": "owner", "status": "success",
        "env": bot["env"], "ext": {"binding": {"online": 77}},
    }
    values.update(changes)
    return BotPublishRecord(**values)


def _reader(world):
    resolver = Mock()
    dispatcher = Mock()
    filesystem = Mock()
    filesystem.read_file = AsyncMock(return_value=b'{"source": "correct"}')
    dispatcher.dispatch_addressed.return_value = filesystem
    service = EngineConfigService(
        bot_repo=world.get(BotRepository), resolver=resolver,
        device_fs_dispatcher=dispatcher, publish_repo=Mock(), binding_repo=Mock(),
    )
    return service, resolver, dispatcher, filesystem


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["u1", "new-owner"])
@pytest.mark.parametrize("locator", ["u1", "staff_u1"])
async def test_old_and_new_publish_rows_keep_correct_coordinates_before_and_after_handoff(world, owner, locator):
    bot = _bot(world, owner=owner)
    record = _record(bot, locator)
    service, resolver, dispatcher, _ = _reader(world)

    assert await service.read_publish_config(record, "openclaw") == {"source": "correct"}

    resolver.resolve_for_binding.assert_called_once_with(77, owner, bot_id=bot["bot_id"])
    assert dispatcher.dispatch_addressed.call_args.kwargs == {
        "namespace": "config", "entity_type": "proj", "entity_id": "staff_u1",
        "bot_id": bot["bot_id"], "engine_type": "openclaw",
    }
    assert record.owner_id == locator
    assert record.source_bot_pk == bot["id"]


@pytest.mark.asyncio
async def test_default_pk_wins_over_another_users_matching_entity(world):
    source = _bot(world, bot_id="default", owner="u1", entity_type="staff")
    _bot(world, bot_id="default", entity="u1", owner="other-user", engine="teclaw")
    record = _record(source, "u1")
    service, resolver, dispatcher, _ = _reader(world)

    await service.read_publish_config(record, "openclaw")

    assert dispatcher.dispatch_addressed.call_args.kwargs["entity_id"] == "staff_u1"
    resolver.resolve_for_binding.assert_called_once_with(77, "u1", bot_id="default")
    published = Mock()
    published.get_by_id.return_value = record
    config_api = Mock()
    config_api.read_publish_config = AsyncMock(return_value={"source": "correct"})
    response = await get_publish_engine_config(
        publish_id=17, user=AuthenticatedUser(id="u1", staffId="u1", operatorName="u1"),
        publish_repo=published, bot_repo=world.get(BotRepository), engine_config_service=config_api,
    )
    assert response.success is True
    config_api.read_publish_config.assert_awaited_once_with(record, "openclaw")


@pytest.mark.asyncio
@pytest.mark.parametrize("locator", ["u1", "staff_u1"])
async def test_orphaned_default_retains_metadata_compatibility_without_rebinding_publish(world, locator):
    replacement = _bot(world, bot_id="default", owner="u1", entity_type="staff")
    record = _record(replacement, locator, source_bot_pk=999999)
    service, resolver, dispatcher, _ = _reader(world)

    await service.read_publish_config(record, "openclaw")

    assert dispatcher.dispatch_addressed.call_args.kwargs["entity_id"] == "staff_u1"
    resolver.resolve_for_binding.assert_called_once_with(77, "u1", bot_id="default")
    assert record.source_bot_pk == 999999
    assert record.ext == {"binding": {"online": 77}}


@pytest.mark.asyncio
@pytest.mark.parametrize("problem,error", [
    ("missing_source", BotAddressNotFoundError),
    ("wrong_source", BotAddressConflictError),
])
async def test_invalid_service_source_does_not_fall_back_to_owner_coordinates(world, problem, error):
    source = _bot(world)
    changes = {"source_bot_pk": 999999} if problem == "missing_source" else {"source_bot_id": "different-bot"}
    record = _record(source, "staff_u1", **changes)
    service, resolver, dispatcher, filesystem = _reader(world)

    with pytest.raises(error):
        await service.read_publish_config(record, "openclaw")

    resolver.resolve_for_binding.assert_not_called()
    dispatcher.dispatch_addressed.assert_not_called()
    filesystem.read_file.assert_not_awaited()


def test_missing_default_fails_closed_on_ambiguous_legacy_locator(world):
    _bot(world, bot_id="default", owner="u1")
    _bot(world, bot_id="default", entity="u1", owner="another-owner")
    from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError

    with pytest.raises(BotLookupAmbiguousError):
        BotAddressResolver(world.get(BotRepository)).resolve_publish_source(
            source_bot_pk=999999, source_bot_id="default", legacy_owner_id="u1",
        )
