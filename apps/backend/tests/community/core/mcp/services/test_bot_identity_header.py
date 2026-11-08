"""Bot identity survives both MCP delivery paths without changing credentials."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.config_compose.services.mcporter_composer import (
    McporterComposer,
)
from agentclaw.community.core.devices.services.mcp_device_payload import (
    convert_to_device_format,
)
from tests.community.core.config_compose.test_collector import _collector, _req
from tests.community.core.mcp.services.test_config_service import _service
from tests.community.core.mcp.services.test_sync_service import (
    _make_plugin,
    _make_sync_service,
)


@pytest.fixture
def config():
    repo = MagicMock()
    repo.get_by_user_and_server_code.return_value = {
        "extra_config": {
            "headers": {"Authorization": "Bearer existing", "CallerToken": "caller"},
        }
    }
    return _service(repo=repo)


def _remote(protocol="STREAMABLE_HTTP"):
    return {
        "serverCode": "mcp.test",
        "runMode": "REMOTE",
        "endpoints": [
            {
                "env": "PROD",
                "networkType": "INTERNET",
                "transportProtocol": protocol,
                "url": "https://mcp.example.test/mcp",
            }
        ],
    }


@pytest.mark.parametrize(
    "engine", ["openclaw", "claude_code", "teclaw", "hermes", "aicoding"]
)
@pytest.mark.parametrize("protocol", ["SSE", "STREAMABLE_HTTP"])
@pytest.mark.parametrize("custom_url", [False, True])
def test_remote_bot_identity_is_engine_and_endpoint_independent(
    config, engine, protocol, custom_url
):
    override = {"url": "https://custom.example.test/mcp"} if custom_url else None

    _, headers, _, _ = config.build_mcp_sync_payload(
        user_id="owner",
        bot_id="bot-1",
        mcp_data=_remote(protocol),
        engine_type=engine,
        bot_override=override,
    )

    assert headers == {
        "Authorization": "Bearer existing",
        "CallerToken": "caller",
        "X-TC-Bot-Id": "bot-1",
    }


def test_bot_identity_overrides_all_configured_header_casings(config, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.mcp.services._defaults.get_default_mcp_servers",
        lambda _engine: [
            {"server_code": "mcp.test", "headers": {"X-TC-BOT-ID": "default"}}
        ],
    )
    monkeypatch.setattr(
        config, "_managed_headers", lambda _server: {"x-TC-bot-ID": "managed"}
    )
    user_headers = {"x-tc-bot-id": "user", "X-Trace": "trace"}
    bot_headers = {"X-Tc-Bot-Id": "override"}

    _, headers, _, _ = config.build_mcp_sync_payload(
        user_id="owner",
        bot_id="bot-1",
        mcp_data=_remote(),
        custom_headers=user_headers,
        bot_override={"headers": bot_headers},
    )

    assert headers == {"X-Trace": "trace", "X-TC-Bot-Id": "bot-1"}
    assert user_headers == {"x-tc-bot-id": "user", "X-Trace": "trace"}
    assert bot_headers == {"X-Tc-Bot-Id": "override"}


def test_concurrent_bots_do_not_share_identity(config):
    def payload(bot_id):
        return config.build_mcp_sync_payload(
            user_id="owner",
            bot_id=bot_id,
            mcp_data=_remote(),
        )[1]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(payload, ["bot-1", "bot-2"])

    assert first["X-TC-Bot-Id"] == "bot-1"
    assert second["X-TC-Bot-Id"] == "bot-2"
    assert (
        "X-TC-Bot-Id"
        not in config.get_user_unified_config("owner", "mcp.test")["headers"]
    )


def test_user_scoped_preflight_without_bot_keeps_existing_headers(config):
    _, headers, _, _ = config.build_mcp_sync_payload(
        user_id="owner", mcp_data=_remote()
    )

    assert headers == {"Authorization": "Bearer existing", "CallerToken": "caller"}


@pytest.mark.parametrize("run_mode_key", ["run_mode", "runMode"])
def test_local_mcp_headers_are_unchanged(config, run_mode_key):
    _, headers, _, _ = config.build_mcp_sync_payload(
        user_id="owner",
        bot_id="bot-1",
        mcp_data={"serverCode": "mcp.test", run_mode_key: "LOCAL"},
    )

    assert headers == {"Authorization": "Bearer existing", "CallerToken": "caller"}


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["SSE", "STREAMABLE_HTTP"])
async def test_device_sync_delivers_bot_identity_in_server_headers(config, protocol):
    plugin = _make_plugin()
    service = _make_sync_service(mcp_config_service=config, plugin=plugin)
    detail = _remote(protocol)

    result = await service.sync_mcp_detail(
        user_id="owner", bot_id="bot-1", mcp_data=detail
    )

    assert result["success"] is True
    plugin.sync_single_mcp.assert_called_once()
    delivered = convert_to_device_format(
        detail, **plugin.sync_single_mcp.call_args.kwargs
    )
    assert delivered.headers == {
        "Authorization": "Bearer existing",
        "CallerToken": "caller",
        "X-TC-Bot-Id": "bot-1",
    }
    assert delivered.transport == ("sse" if protocol == "SSE" else "http")


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "teclaw"])
def test_whole_artifact_delivers_bot_identity_in_server_headers(config, engine):
    skills = MagicMock()
    skills.collect_bot_active_mcps.return_value = [{"server_code": "mcp.test"}]
    skills.mcp_center.get_mcp_detail.return_value = _remote()
    collector = _collector(skill_set_service=skills, mcp_config_service=config)

    manifest = McporterComposer().compose(collector.mcps(_req(engine)))

    assert len(manifest.servers) == 1
    assert manifest.servers[0].headers == {
        "Authorization": "Bearer existing",
        "CallerToken": "caller",
        "X-TC-Bot-Id": "bot1",
    }


def test_whole_artifact_stdio_has_no_bot_identity_header(config):
    skills = MagicMock()
    skills.collect_bot_active_mcps.return_value = [{"server_code": "mcp.test"}]
    skills.mcp_center.get_mcp_detail.return_value = {
        "serverCode": "mcp.test",
        "runMode": "LOCAL",
        "stdioConfigs": [
            {"command": "test-mcp", "envVariables": {"EXISTING": "value"}}
        ],
    }
    collector = _collector(skill_set_service=skills, mcp_config_service=config)

    manifest = McporterComposer().compose(collector.mcps(_req()))

    assert manifest.servers[0].transport == "stdio"
    assert manifest.servers[0].headers == {}
    assert manifest.servers[0].env == {"EXISTING": "value"}
