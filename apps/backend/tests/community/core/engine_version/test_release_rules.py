"""Release branch identity, missing metadata and command failures are distinct."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.engine_version.models import (
    EngineVersionConfig,
)
from agentclaw.community.core.bot_management.engine_version.service import (
    EngineVersionService,
)
from tests.community.core.engine_version.test_engine_version_service import dependencies


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
        ("REL20261009", "0.1.153", "unknown", "unknown"),
        ("REL20261009", "a\nb", "unknown", "unknown"),
    ],
)
async def test_release_identity_is_the_complete_branch(
    dependencies, current, target, comparison, hint
):
    bots, templates, bindings, resolver, transport = dependencies
    transport.invoke.return_value["data"]["stdout"] = current + "\n"
    service = EngineVersionService(
        bots=bots,
        templates=templates,
        bindings=bindings,
        resolver=resolver,
        transport=transport,
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
    bots, templates, bindings, resolver, transport = dependencies
    transport.invoke.return_value = response
    service = EngineVersionService(
        bots=bots,
        templates=templates,
        bindings=bindings,
        resolver=resolver,
        transport=transport,
        collaborators=MagicMock(),
        list_devices=MagicMock(),
        config=EngineVersionConfig("REL20261014"),
    )
    result = await service.inspect(bot_id="bot-1", owner_id="owner", caller_id="owner")
    assert result["detection"]["status"] == "read_failed"
    assert result["restart_hint"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bot_type", "ext", "template", "expected", "reason"),
    [
        (
            "personal",
            {"sbot_pin_image": True, "sbot_docker_image": "private:old"},
            {},
            "restricted",
            "image_pinned",
        ),
        (
            "service",
            {"sbot_pin_image": True, "sbot_docker_image": "private:old"},
            {},
            "recommended",
            None,
        ),
        (
            "service",
            {"sbot_use_default_image": True},
            {"image": "private:old"},
            "restricted",
            "explicit_image_unverified",
        ),
        (
            "personal",
            {},
            {"image_config": {"name": "private:old"}},
            "restricted",
            "explicit_image_unverified",
        ),
        (
            "personal",
            {},
            {"realtime_pull": True, "image": "private:old"},
            "restricted",
            "explicit_image_unverified",
        ),
    ],
)
async def test_effective_restart_image_limits_do_not_hide_version_difference(
    dependencies, bot_type, ext, template, expected, reason
):
    bots, templates, bindings, resolver, transport = dependencies
    bots.get_by_id_and_owner.return_value.update(bot_type=bot_type, ext=ext)
    templates.get_by_bot_id.return_value = {"ext": template}
    service = EngineVersionService(
        bots=bots,
        templates=templates,
        bindings=bindings,
        resolver=resolver,
        transport=transport,
        collaborators=MagicMock(),
        list_devices=MagicMock(),
        config=EngineVersionConfig("REL20261014"),
    )
    result = await service.inspect(bot_id="bot-1", owner_id="owner", caller_id="owner")
    assert result["comparison"] == "older"
    assert result["restart_hint"] == expected
    assert result.get("restart_reason") == reason
    assert bots.get_by_id_and_owner.return_value["ext"] == ext
    assert templates.get_by_bot_id.return_value == {"ext": template}


@pytest.mark.asyncio
@pytest.mark.parametrize(("bot_type", "ext", "template", "expected", "reason"), [
    ("personal", {"sbot_pin_image": True}, {}, "unknown", "policy_unknown"),
    ("personal", {"sbot_pin_image": True, "sbot_docker_image": "private:old"}, {"image": "other:old"}, "restricted", "image_pinned"),
])
async def test_personal_pin_is_effective_and_malformed_policy_is_unknown(dependencies, bot_type, ext, template, expected, reason):
    bots, templates, bindings, resolver, transport = dependencies
    bots.get_by_id_and_owner.return_value.update(bot_type=bot_type, ext=ext)
    templates.get_by_bot_id.return_value = {"ext": template}
    service = EngineVersionService(
        bots=bots, templates=templates, bindings=bindings, resolver=resolver,
        transport=transport, collaborators=MagicMock(), list_devices=MagicMock(),
        config=EngineVersionConfig("REL20261014"),
    )
    result = await service.inspect(bot_id="bot-1", owner_id="owner", caller_id="owner")
    assert result["comparison"] == "older"
    assert result["restart_hint"] == expected
    assert result["restart_reason"] == reason
