"""Channel creation and draft collection must agree when Owner != entity."""

import pytest

from agentclaw.community.api.channel_service import ChannelServiceProtocol
from agentclaw.community.core.config_compose.models import ComposeRequest
from agentclaw.community.core.config_compose.services.collector import ConfigComposerInputCollector
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.chat import ChannelRepository
from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401


@pytest.mark.parametrize("bot_type,bot_id,owner_id", [
    ("personal", "personal-channels", "u1"),
    ("personal", "default", "u1"),
    ("service", "default", "u1"),
    ("desktop", "desktop-channels", "u1"),
    ("service", "service-channels", "u1"),
    ("service", "handed-off-channels", "new-owner"),
])
@pytest.mark.parametrize("stage", [None, "", "draft"])
def test_compose_preserves_channels_under_the_storage_identity(world, bot_type, bot_id, owner_id, stage):
    entity_id = "staff_u1"
    world.get(BotRepository).insert({
        "bot_id": bot_id, "bot_name": "Channel identity regression",
        "owner_id": owner_id, "entity_id": entity_id, "entity_type": "staff",
        "creator_id": "u1", "bot_type": bot_type, "active_engine": "teclaw",
        "status": "ACTIVE",
    })
    service = world.get(ChannelServiceProtocol)
    channels = world.get(ChannelRepository)
    channel_id = service.create_channel(
        type="dingding", description="own channel", identity_id=owner_id,
        bind_bot_id=bot_id, status="1", stage=stage, config={"client_id": "own-client"},
    )
    stable = bot_type == "service" and bot_id != "default"
    expected_identity = entity_id if stable else owner_id
    assert channels.get_by_id(channel_id).identity_id == expected_identity

    # Querying both identities would silently collect this unrelated channel.
    other_identity = owner_id if stable else entity_id
    channels.insert_channel(
        type="dingding", description="other identity", identity_id=other_identity,
        bind_bot_id=bot_id, status="1", stage=stage, config={"client_id": "other-client"},
    )
    channels.insert_channel(
        type="dingding", description="shared default", identity_id="aideskdingding",
        bind_bot_id=bot_id, status="1", stage=stage, config={"client_id": "shared-client"},
    )
    channels.insert_channel(
        type="dingding", description="published stage", identity_id=expected_identity,
        bind_bot_id=bot_id, status="1", stage="verify", config={"client_id": "verify-client"},
    )

    overrides = world.get(ConfigComposerInputCollector).engine_overrides(ComposeRequest(
        bot_id=bot_id, entity_id=entity_id, user_id=owner_id, engine_type="teclaw",
    ))
    accounts = overrides["channels"]["dingding"]["accounts"]
    assert {account["client_id"] for account in accounts} == {"own-client", "shared-client"}


def test_missing_bot_keeps_legacy_channel_lookup(world):
    world.get(ChannelRepository).insert_channel(
        type="dingding", description="historical channel", identity_id="u1",
        bind_bot_id="missing-bot", status="1", config={"client_id": "legacy-client"},
    )
    overrides = world.get(ConfigComposerInputCollector).engine_overrides(ComposeRequest(
        bot_id="missing-bot", entity_id="staff_u1", user_id="u1", engine_type="teclaw",
    ))
    assert overrides["channels"]["dingding"]["accounts"][0]["client_id"] == "legacy-client"
