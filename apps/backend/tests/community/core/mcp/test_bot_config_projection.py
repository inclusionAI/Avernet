"""Custom Bot URL delivery through the existing per-engine projection seams."""

from secrets import token_hex
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.addressing import ResolvedBotContext
from agentclaw.community.core.config_compose.services.mcporter_composer import (
    McporterComposer,
)
from agentclaw.community.core.devices.services.mcp_device_payload import (
    convert_to_device_format,
)
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.di.config import McpRuntimeCredentialsConfig
from tests.community.core.config_compose.test_collector import _collector, _req
from tests.community.core.mcp.services.test_sync_service import (
    _make_plugin,
    _make_sync_service,
)

BOT_URL = "https://bot.example.invalid/mcp"
OWNER_URL = "https://owner.example.invalid/mcp"


def _config(bot_url):
    user_repo = MagicMock()
    user_repo.get_by_user_and_server_code.return_value = {
        "extra_config": {
            "url": OWNER_URL,
            "api_key": f"authorization={token_hex(16)}",
            "headers": {"X-Region": "owner", "X-Trace": "on"},
        }
    }
    bot_repo = MagicMock()
    bot_repo.get_by_bot_and_server_code.return_value = {
        "headers": {"x-region": "bot"},
        **({"url": bot_url} if bot_url else {}),
    }
    config = MCPConfigService(
        user_mcp_config_repo=user_repo,
        bot_mcp_config_repo=bot_repo,
        mcp_center=MagicMock(),
        bot_repo=MagicMock(),
        capability_reader=MagicMock(),
        mcp_runtime_credentials=McpRuntimeCredentialsConfig(),
        secret_resolver=MagicMock(),
    )
    return config, bot_repo


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_url", [BOT_URL, None])
@pytest.mark.parametrize(
    "catalogued, config_operation", [(True, True), (False, True), (True, False)]
)
async def test_single_bot_projection_builds_payload_with_custom_url(
    bot_url, catalogued, config_operation
):
    config, bot_repo = _config(bot_url)
    service = _make_sync_service(mcp_config_service=config)
    bot = {"id": 42, "entity_id": "entity", "owner_id": "owner", "bot_id": "shared"}
    service.bot_repository.get_by_id_and_entity.side_effect = None
    service.bot_repository.get_by_id_and_entity.return_value = bot
    plugin = _make_plugin()
    entry = {"serverCode": "weather"}
    if catalogued:
        entry.update(runMode="REMOTE", endpoints=[])
        config.mcp_center.get_mcp_detail.return_value = entry

    result = await service.sync_mcp_details_for_bot(
        user_id="owner",
        entity_id="entity",
        bot_id="shared",
        mcp_entries=[entry],
        device_sync=plugin,
        config_target=(
            ResolvedBotContext(42, "shared", "entity", "owner", bot)
            if config_operation
            else None
        ),
    )

    assert result["success"]
    plugin.sync_single_mcp.assert_called_once()
    args, kwargs = plugin.sync_single_mcp.call_args
    payload = convert_to_device_format(*args, **kwargs).to_dict()
    assert payload["url"] == (bot_url or OWNER_URL)
    assert payload["headers"] == {
        "x-region": "bot",
        "X-Trace": "on",
        "X-TC-Bot-Id": "shared",
    }
    bot_repo.get_by_bot_and_server_code.assert_called_once_with(
        bot_id="shared", owner_id="entity", server_code="weather"
    )


@pytest.mark.parametrize("detail", [None, {"runMode": "REMOTE", "endpoints": []}])
def test_teclaw_compose_resolves_custom_url_without_a_center_endpoint(detail):
    config, _ = _config(BOT_URL)
    skills = MagicMock()
    skills.collect_bot_active_mcps.return_value = [{"server_code": "weather"}]
    skills.mcp_center.get_mcp_detail.return_value = detail
    collector = _collector(skill_set_service=skills, mcp_config_service=config)

    (entry,) = collector.mcps(_req(engine_type="teclaw"))
    server = McporterComposer().compose_server(entry)
    assert server.endpoint == BOT_URL
    assert server.headers["X-Trace"] == "on"
    assert server.headers["x-region"] == "bot"
