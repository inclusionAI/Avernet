"""Channel resources survive Owner changes without relaxing Owner-only APIs."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService, BotNotFoundError
from agentclaw.community.core.channel.json_config_utils import JsonConfigFile
from agentclaw.community.core.channel.services.channel_service import ChannelService
from agentclaw.community.core.config_compose.models import ComposeRequest
from agentclaw.community.core.config_compose.services.collector import ConfigComposerInputCollector
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.chat import ChannelRepository
from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401


@pytest.fixture
def handed_off(world):
    bots = world.get(BotRepository)
    bots.insert({
        "bot_id": "channel-handoff", "bot_name": "Channel handoff",
        "owner_id": "new-owner", "entity_id": "old-owner", "entity_type": "staff",
        "creator_id": "old-owner", "bot_type": "service", "active_engine": "openclaw",
        "status": "ACTIVE",
    })
    channels = world.get(ChannelRepository)
    for stage in (None, "verify", "online"):
        channels.insert_channel(
            type="dingding", description="handoff regression", identity_id="old-owner",
            bind_bot_id="channel-handoff", status="1", stage=stage,
            config={"client_id": stage or "draft", "client_secret": "test"},
        )
    service = ChannelService(
        repository=channels, bot_service=world.get(BotService), resolver=Mock(),
        device_fs_dispatcher=Mock(), device_sync_dispatcher=Mock(), bcs_client=Mock(),
    )
    return SimpleNamespace(service=service, bots=bots, channels=channels, world=world)


@pytest.mark.asyncio
@pytest.mark.parametrize("locator", ["old-owner", "new-owner"])
async def test_stage_generation_uses_current_owner_but_retains_entity_channels(handed_off, locator):
    service = handed_off.service
    template = JsonConfigFile("openclaw.json", {"channels": {"dingtalk": {}}})
    with patch.object(JsonConfigFile, "load", AsyncMock(return_value=template)):
        result = await service.generate_openclaw_configs(bot_id="channel-handoff", owner_id=locator)
    service._resolver.resolve_for_bot.assert_called_once_with("channel-handoff", "new-owner")
    assert "verify" in json.loads(result.verify)["channels"]["dingtalk"]["accounts"]
    assert "online" in json.loads(result.online)["channels"]["dingtalk"]["accounts"]
    # A compatible resource resolver must not turn the strict Owner API into an alias lookup.
    with pytest.raises(BotNotFoundError):
        handed_off.world.get(BotService).get_bot("channel-handoff", "old-owner")


def test_new_owner_lists_and_creates_channels_under_original_entity(handed_off):
    service = handed_off.service
    assert len(service.list_channels(type="dingding", identity_id="new-owner", bind_bot_id="channel-handoff")) == 3
    channel_id = service.create_channel(
        type="dingding", description="new channel", identity_id="new-owner",
        bind_bot_id="channel-handoff", config={"client_id": "added"},
    )
    assert handed_off.channels.get_by_id(channel_id).identity_id == "old-owner"


@pytest.mark.asyncio
async def test_existing_draft_channel_sync_resolves_current_owner(handed_off):
    record = handed_off.channels.get_by_type_and_identity_ids(
        type="dingding", identity_ids=["old-owner"], bind_bot_id="channel-handoff",
    )
    draft = next(row for row in record if row.stage is None)
    template = JsonConfigFile("openclaw.json", {"channels": {"dingtalk": {}}})
    with patch.object(JsonConfigFile, "load", AsyncMock(return_value=template)), \
         patch.object(template, "save", AsyncMock()):
        assert await handed_off.service.sync_channel_to_openclaw(draft.id, action="apply")
    handed_off.service._resolver.resolve_for_bot.assert_called_once_with("channel-handoff", "new-owner")


def test_teclaw_detection_and_compose_keep_existing_channels(handed_off):
    handed_off.bots.update_by_owner("channel-handoff", "new-owner", {"active_engine": "teclaw"})
    assert handed_off.service._is_teclaw_bot("channel-handoff", "old-owner")
    overrides = handed_off.world.get(ConfigComposerInputCollector).engine_overrides(ComposeRequest(
        bot_id="channel-handoff", entity_id="old-owner", user_id="new-owner", engine_type="teclaw",
    ))
    assert [a["client_id"] for a in overrides["channels"]["dingding"]["accounts"]] == ["draft"]
