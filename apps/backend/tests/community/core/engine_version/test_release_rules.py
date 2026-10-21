"""Release branch identity, missing metadata and command failures are distinct."""

from functools import partial
from types import SimpleNamespace
from agentclaw.community.core.bot_inventory.types import BotAction, DisplayState
from agentclaw.community.adapters.engine_release_probe import probe_engine_release
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.engine_version.models import (
    EngineVersionConfig,
)
from agentclaw.community.core.bot_management.engine_version.service import (
    EngineVersionService,
)
from tests.community.core.engine_version.test_engine_version_service import (
    dependencies as dependencies,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current", "target", "comparison", "hint"),
    [
        ("REL20261014", "REL20261014", "same", "none"),
        ("REL20261015", "REL20261014", "newer", "none"),
        ("REL20261009", "REL20261014", "older", "recommended"),
        ("dev", "dev", "same", "none"),
        ("REL20261009_fix", "REL20261014", "unknown", "unknown"),
        ("REL20260230", "REL20261014", "unknown", "unknown"),
        ("dev", "test", "unknown", "unknown"),
        ("", "REL20261014", "legacy", "recommended"),
        ("0.1.153", "REL20261014", "legacy", "recommended"),
        ("REL20261009", "", "unknown", "unknown"),
        ("", "", "unknown", "unknown"),
        ("0.1.153", "", "unknown", "unknown"),
        ("REL20261009", "0.1.153", "unknown", "unknown"),
        ("REL20261009", "a\nb", "unknown", "unknown"),
    ],
)
async def test_release_identity_is_the_complete_branch(
    dependencies, current, target, comparison, hint
):
    bots, bindings, resolver, transport = dependencies
    transport.invoke.return_value["data"]["stdout"] = current + "\n"
    service = EngineVersionService(
        lifecycle=MagicMock(),
        bots=bots,
        bindings=bindings,
        resolver=resolver,
        probe=partial(probe_engine_release, transport),
        collaborators=MagicMock(),
        list_devices=MagicMock(),
        config=EngineVersionConfig(target),
    )
    result = await service.inspect(bot_id="bot-1", owner_id="owner", caller_id="owner")
    assert result["comparison"] == comparison
    assert result["restart_hint"] == hint
    if current in {"", "0.1.153"}:
        assert result["detection"]["status"] == "legacy"
        assert "release_id" not in result.get("current", {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"success": False},
        {},
        {"success": True, "data": []},
        {"success": True, "data": {"stdout": "", "stderr": "error", "exit_code": 1}},
        {"success": True, "data": {"stdout": "", "stderr": "", "exit_code": 127}},
        {"success": True, "data": {"stdout": "", "stderr": "", "exit_code": -1}},
        {
            "success": True,
            "data": {"stdout": "REL20261009\n\n", "stderr": "", "exit_code": 0},
        },
    ],
)
async def test_failed_probe_is_never_a_legacy_bot(dependencies, response):
    bots, bindings, resolver, transport = dependencies
    transport.invoke.return_value = response
    service = EngineVersionService(
        bots=bots,
        bindings=bindings,
        resolver=resolver,
        probe=partial(probe_engine_release, transport),
        collaborators=MagicMock(),
        list_devices=MagicMock(),
        config=EngineVersionConfig("REL20261014"),
        lifecycle=SimpleNamespace(cards_for_bots=lambda **_: {"bot-1": [SimpleNamespace(display_state=DisplayState.SERVICE_DRAFT, actions=[BotAction.RESTART])]}),
    )
    result = await service.inspect(bot_id="bot-1", owner_id="owner", caller_id="owner")
    assert result["detection"]["status"] == "read_failed"
    assert result["restart_hint"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["openclaw", "hermes"])
@pytest.mark.parametrize("provider", ["arca", "baas", "local"])
@pytest.mark.parametrize("bot_type", ["personal", "service"])
@pytest.mark.parametrize(
    ("current", "exit_code", "comparison", "hint"),
    [
        ("REL20261009\n", 0, "older", "recommended"),
        ("", 1, "legacy", "recommended"),
        ("", 0, "legacy", "recommended"),
        ("0.1.153\n", 0, "legacy", "recommended"),
        ("REL20261014\n", 0, "same", "none"),
        ("REL20261015\n", 0, "newer", "none"),
        ("dev\n", 0, "unknown", "unknown"),
        ("", 127, "unknown", "unknown"),
    ],
)
async def test_hints_depend_on_release_facts_not_provider_or_image_pin(
    dependencies, engine, provider, bot_type, current, exit_code, comparison, hint
):
    bots, bindings, resolver, transport = dependencies
    ext = {"sbot_pin_image": True, "sbot_docker_image": "fixture:old"}
    bots.get_by_id_and_owner.return_value.update(
        active_engine=engine, bot_type=bot_type, ext=ext
    )
    bindings.get_active_by_bot_and_owner.return_value.device_provider = provider
    transport.invoke.return_value["data"].update(stdout=current, exit_code=exit_code)
    service = EngineVersionService(
        bots=bots,
        bindings=bindings,
        resolver=resolver,
        probe=partial(probe_engine_release, transport),
        collaborators=MagicMock(),
        list_devices=MagicMock(return_value=[{"device_uuid": "one"}]),
        config=EngineVersionConfig("REL20261014"),
        lifecycle=SimpleNamespace(cards_for_bots=lambda **_: {"bot-1": [SimpleNamespace(display_state=DisplayState.SERVICE_DRAFT, actions=[BotAction.RESTART])]}),
    )
    result = await service.inspect(bot_id="bot-1", owner_id="owner", caller_id="owner")
    assert result["comparison"] == comparison
    assert result["restart_hint"] == hint
    assert "restart_reason" not in result
    assert bots.get_by_id_and_owner.return_value["ext"] == ext
    transport.invoke.assert_awaited_once()
