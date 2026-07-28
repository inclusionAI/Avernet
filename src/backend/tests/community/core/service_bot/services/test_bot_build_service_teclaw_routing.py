"""Tests for teclaw routing in BotBuildService.release / upgrade.

The publish path provisions the container via BaaS. For teclaw it routes to the
non-mount create_teclaw_bot / update_teclaw_bot carrying the frozen artifact;
for ARCA it keeps the existing create_bot / upgrade_bot (migration_path) path,
byte-for-byte unchanged. Provider is resolved by querying baas.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildService,
    BotBuildServiceError,
)
from agentclaw.community.core.service_bot.services.deploy.engine_ext_stage import (
    DeliveryArtifact,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.core.devices.services.baas_template_resolver import (
    BaasTemplateResolution,
)

_BOT = {"bot_id": "b", "entity_id": "u", "entity_type": "staff", "bot_name": "B"}
_ARTIFACT = {"schema_version": 2, "skills": []}


def _svc(
    provider: str,
    common_config_service: MagicMock | None = None,
    baas_template_resolver: MagicMock | None = None,
) -> tuple[BotBuildService, MagicMock]:
    baas = MagicMock()
    baas.resolve_container_provider.return_value = provider
    baas.create_teclaw_bot.return_value = {"bot_uuid": "BOT-t", "publish_id": 5}
    baas.update_teclaw_bot.return_value = {"bot_uuid": "BOT-t", "publish_id": 6}
    baas.create_bot.return_value = {"bot_uuid": "BOT-a", "publish_id": 7}
    baas.upgrade_bot.return_value = {"bot_uuid": "BOT-a", "publish_id": 8}
    svc = BotBuildService(
        device_service=MagicMock(),
        baas_service=baas,
        path_factory=MagicMock(),
        passport_plugin=MagicMock(),
        device_binding_repo=MagicMock(),
        sandbox_registry=MagicMock(),
        bot_repository=MagicMock(),
        teclaw_template_uuid="teclaw-tpl",
        baas_template_resolver=baas_template_resolver or MagicMock(),
        channel_service=MagicMock(),
        common_whitelist_service=MagicMock(),
    )
    svc._passport_plugin.query_token.return_value = "passport-token"
    return svc, baas


def _template_resolver() -> MagicMock:
    resolver = MagicMock()
    resolver.resolve_template.return_value = BaasTemplateResolution(
        template_uid="claude_code_bot_template",
        template_uuid="TEMPLATE-claude-code",
        source="system_config",
    )
    return resolver


@pytest.mark.unit
def test_release_routes_teclaw_to_create_teclaw_bot():
    svc, baas = _svc("teclaw")
    svc.release(
        _BOT, user_id="u1", migration_path="", publish_stage=PublishStage.VERIFY,
        delivery=DeliveryArtifact(_ARTIFACT),
    )
    baas.create_teclaw_bot.assert_called_once()
    ck = baas.create_teclaw_bot.call_args
    assert ck.kwargs["config_artifact"] == _ARTIFACT
    assert ck.kwargs["template_uuid"] == "teclaw-tpl"
    assert "agent_pass_token" not in ck.kwargs
    svc._passport_plugin.query_token.assert_called_once_with("b", "u")
    baas.create_bot.assert_not_called()


@pytest.mark.unit
def test_release_routes_arca_to_create_bot_unchanged():
    svc, baas = _svc("baas")
    svc.release(
        _BOT, user_id="u1", migration_path="/m/1", publish_stage=PublishStage.VERIFY,
    )
    baas.create_bot.assert_called_once()
    assert baas.create_bot.call_args.kwargs["migration_path"] == "/m/1"
    assert baas.create_bot.call_args.kwargs["device_count"] == 1
    baas.create_teclaw_bot.assert_not_called()


@pytest.mark.unit
def test_release_routes_arca_with_template_uuid_from_system_config():
    resolver = _template_resolver()
    svc, baas = _svc("baas", baas_template_resolver=resolver)
    bot = {
        **_BOT,
        "active_engine": "claude_code",
        "bot_type": "service",
        "template_type": "normalCC",
    }

    svc.release(
        bot, user_id="u1", migration_path="/m/1", publish_stage=PublishStage.VERIFY,
    )

    resolver.resolve_template.assert_called_once()
    template_kwargs = resolver.resolve_template.call_args.kwargs
    assert template_kwargs["bot_id"] == "b"
    assert template_kwargs["user_id"] == "u1"
    assert template_kwargs["bot_type"] == "service"
    assert template_kwargs["engine_type"] == "claude_code"
    assert template_kwargs["template_type"] == "normalCC"
    assert template_kwargs["template_config"] is None
    assert template_kwargs["env"]
    resolver.resolve_template_uid.assert_not_called()
    resolver.resolve_template_uuid.assert_not_called()
    resolver.resolve_template.assert_called_once_with(
        bot_id="b",
        user_id="u1",
        env=template_kwargs["env"],
        bot_type="service",
        engine_type="claude_code",
        template_type="normalCC",
        template_config=None,
    )
    assert baas.create_bot.call_args.kwargs["template_uuid"] == "TEMPLATE-claude-code"


@pytest.mark.unit
def test_release_teclaw_with_no_artifact_raises():
    """teclaw delivery IS the config_artifact — a missing one must fail loudly,
    not silently provision a container with empty config."""
    svc, baas = _svc("teclaw")
    with pytest.raises(BotBuildServiceError, match="config_artifact"):
        svc.release(_BOT, user_id="u1", migration_path="")
    baas.create_teclaw_bot.assert_not_called()


@pytest.mark.unit
def test_upgrade_routes_teclaw_to_update_teclaw_bot():
    svc, baas = _svc("teclaw")
    svc.upgrade(
        "BOT-t", _BOT, user_id="u1", migration_path="",
        publish_stage=PublishStage.ONLINE, delivery=DeliveryArtifact(_ARTIFACT),
    )
    baas.update_teclaw_bot.assert_called_once()
    uk = baas.update_teclaw_bot.call_args
    assert uk.args[0] == "BOT-t"
    assert uk.kwargs["config_artifact"] == _ARTIFACT
    assert uk.kwargs["template_uuid"] == "teclaw-tpl"
    assert "agent_pass_token" not in uk.kwargs
    svc._passport_plugin.query_token.assert_not_called()
    baas.update_teclaw_outbound_rule_by_bot_uuid.assert_not_called()
    baas.upgrade_bot.assert_not_called()


@pytest.mark.unit
def test_refresh_teclaw_mcp_outbound_rule_updates_rule():
    svc, baas = _svc("teclaw")

    result = svc.refresh_teclaw_mcp_outbound_rule(bot_uuid="BOT-t", bot=_BOT)

    assert result is True
    svc._passport_plugin.query_token.assert_called_once_with("b", "u")
    baas.update_teclaw_outbound_rule_by_bot_uuid.assert_called_once_with(
        "BOT-t",
        agent_pass_token=svc._passport_plugin.query_token.return_value,
    )


@pytest.mark.unit
def test_refresh_teclaw_mcp_outbound_rule_skips_non_teclaw():
    svc, baas = _svc("baas")

    result = svc.refresh_teclaw_mcp_outbound_rule(bot_uuid="BOT-a", bot=_BOT)

    assert result is False
    svc._passport_plugin.query_token.assert_not_called()
    baas.update_teclaw_outbound_rule_by_bot_uuid.assert_not_called()


@pytest.mark.unit
def test_refresh_teclaw_mcp_outbound_rule_skips_when_context_missing():
    svc, baas = _svc("teclaw")

    result = svc.refresh_teclaw_mcp_outbound_rule(
        bot_uuid="BOT-t",
        bot={"bot_id": "b"},
    )

    assert result is False
    svc._passport_plugin.query_token.assert_not_called()
    baas.update_teclaw_outbound_rule_by_bot_uuid.assert_not_called()


@pytest.mark.unit
def test_upgrade_teclaw_keeps_token_out_of_update_payload_when_query_fails():
    svc, baas = _svc("teclaw")
    svc._passport_plugin.query_token.side_effect = RuntimeError("boom")

    result = svc.upgrade(
        "BOT-t", _BOT, user_id="u1", migration_path="",
        publish_stage=PublishStage.ONLINE, delivery=DeliveryArtifact(_ARTIFACT),
    )

    assert result == {"bot_uuid": "BOT-t", "publish_id": 6}
    baas.update_teclaw_bot.assert_called_once()
    assert "agent_pass_token" not in baas.update_teclaw_bot.call_args.kwargs
    svc._passport_plugin.query_token.assert_not_called()
    baas.update_teclaw_outbound_rule_by_bot_uuid.assert_not_called()


@pytest.mark.unit
def test_refresh_teclaw_mcp_outbound_rule_skips_when_token_query_fails():
    svc, baas = _svc("teclaw")
    svc._passport_plugin.query_token.side_effect = RuntimeError("boom")

    result = svc.refresh_teclaw_mcp_outbound_rule(bot_uuid="BOT-t", bot=_BOT)

    assert result is False
    svc._passport_plugin.query_token.assert_called_once_with("b", "u")
    baas.update_teclaw_outbound_rule_by_bot_uuid.assert_not_called()


@pytest.mark.unit
def test_refresh_teclaw_mcp_outbound_rule_skips_when_agent_pass_token_empty():
    svc, baas = _svc("teclaw")
    svc._passport_plugin.query_token.return_value = ""

    result = svc.refresh_teclaw_mcp_outbound_rule(bot_uuid="BOT-t", bot=_BOT)

    assert result is False
    svc._passport_plugin.query_token.assert_called_once_with("b", "u")
    baas.update_teclaw_outbound_rule_by_bot_uuid.assert_not_called()


@pytest.mark.unit
def test_refresh_teclaw_mcp_outbound_rule_returns_false_when_update_fails():
    svc, baas = _svc("teclaw")
    baas.update_teclaw_outbound_rule_by_bot_uuid.side_effect = RuntimeError("rule down")

    result = svc.refresh_teclaw_mcp_outbound_rule(bot_uuid="BOT-t", bot=_BOT)

    assert result is False
    baas.update_teclaw_outbound_rule_by_bot_uuid.assert_called_once_with(
        "BOT-t",
        agent_pass_token=svc._passport_plugin.query_token.return_value,
    )


@pytest.mark.unit
def test_upgrade_teclaw_with_no_artifact_raises():
    """Same guard on the re-publish path — never re-deliver empty config to the
    existing container."""
    svc, baas = _svc("teclaw")
    with pytest.raises(BotBuildServiceError, match="config_artifact"):
        svc.upgrade(
            "BOT-t", _BOT, user_id="u1", migration_path="",
            publish_stage=PublishStage.ONLINE,
        )
    baas.update_teclaw_bot.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("error_code", ["BOT_NOT_FOUND", "DEVICE_NOT_FOUND"])
def test_upgrade_teclaw_gone_bot_returns_structured_failure(error_code):
    """Regression (#435): a teclaw upgrade against a destroyed device answers
    with ``DEVICE_NOT_FOUND`` (a STOP physically destroys the TeClaw bot). Both
    that and ``BOT_NOT_FOUND`` must surface as a structured ``success: False``
    result — not a raised ``BotBuildServiceError`` — so the deploy atom
    classifies it and self-heals with a fresh first release."""
    from agentclaw.community.core.service_bot.services.baas_service import (
        BaasServiceError,
    )

    svc, baas = _svc("teclaw")
    err = BaasServiceError(f"BaaS API error: 404 - {error_code}")
    err.response = MagicMock()
    err.response.json.return_value = {
        "detail": {"error_code": error_code, "message": "bot not found"}
    }
    baas.update_teclaw_bot.side_effect = err

    result = svc.upgrade(
        "BOT-t", _BOT, user_id="u1", migration_path="",
        publish_stage=PublishStage.ONLINE, delivery=DeliveryArtifact(_ARTIFACT),
    )

    assert result == {
        "success": False,
        "error_code": error_code,
        "message": "bot not found",
        "bot_uuid": "BOT-t",
    }


@pytest.mark.unit
def test_upgrade_routes_arca_to_upgrade_bot_unchanged():
    svc, baas = _svc("baas")
    svc.upgrade(
        "BOT-a", _BOT, user_id="u1", migration_path="/m/2",
        publish_stage=PublishStage.ONLINE,
    )
    baas.upgrade_bot.assert_called_once()
    assert baas.upgrade_bot.call_args.kwargs["migration_path"] == "/m/2"
    assert baas.upgrade_bot.call_args.kwargs["device_count"] == 1
    baas.update_teclaw_bot.assert_not_called()


@pytest.mark.unit
def test_upgrade_routes_arca_with_template_uuid_from_system_config():
    resolver = _template_resolver()
    svc, baas = _svc("baas", baas_template_resolver=resolver)
    bot = {
        **_BOT,
        "active_engine": "claude_code",
        "bot_type": "service",
        "template_type": "normalCC",
    }

    svc.upgrade(
        "BOT-a", bot, user_id="u1", migration_path="/m/2",
        publish_stage=PublishStage.ONLINE,
    )

    resolver.resolve_template.assert_called_once()
    template_kwargs = resolver.resolve_template.call_args.kwargs
    assert template_kwargs["bot_id"] == "b"
    assert template_kwargs["user_id"] == "u1"
    assert template_kwargs["bot_type"] == "service"
    assert template_kwargs["engine_type"] == "claude_code"
    assert template_kwargs["template_type"] == "normalCC"
    assert template_kwargs["template_config"] is None
    assert template_kwargs["env"]
    resolver.resolve_template_uid.assert_not_called()
    resolver.resolve_template_uuid.assert_not_called()
    resolver.resolve_template.assert_called_once_with(
        bot_id="b",
        user_id="u1",
        env=template_kwargs["env"],
        bot_type="service",
        engine_type="claude_code",
        template_type="normalCC",
        template_config=None,
    )
    assert baas.upgrade_bot.call_args.kwargs["template_uuid"] == "TEMPLATE-claude-code"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upgrade_async_wraps_upgrade():
    svc, baas = _svc("baas")

    result = await svc.upgrade_async(
        "BOT-a", _BOT, user_id="u1", migration_path="/m/2",
        publish_stage=PublishStage.ONLINE,
    )

    assert result == {"bot_uuid": "BOT-a", "publish_id": 8}
    baas.upgrade_bot.assert_called_once()


@pytest.mark.unit
def test_release_device_count_does_not_apply_to_teclaw_path():
    common_config_service = MagicMock()
    common_config_service.get_value.side_effect = [None, 5]
    svc, baas = _svc("teclaw", common_config_service=common_config_service)

    svc.release(
        {**_BOT, "env": "dev"},
        user_id="u1",
        migration_path="",
        device_count=9,
        publish_stage=PublishStage.VERIFY,
        delivery=DeliveryArtifact(_ARTIFACT),
    )

    assert baas.create_teclaw_bot.call_args.kwargs["device_count"] == 1


@pytest.mark.unit
def test_upgrade_device_count_does_not_apply_to_teclaw_path():
    common_config_service = MagicMock()
    common_config_service.get_value.return_value = 7
    svc, baas = _svc("teclaw", common_config_service=common_config_service)

    svc.upgrade(
        "BOT-t",
        {**_BOT, "env": "dev"},
        user_id="u1",
        migration_path="",
        device_count=9,
        publish_stage=PublishStage.ONLINE,
        delivery=DeliveryArtifact(_ARTIFACT),
    )

    assert baas.update_teclaw_bot.call_args.kwargs["device_count"] == 1


@pytest.mark.unit
def test_retire_superseded_bot_calls_destroy_idempotently():
    svc, baas = _svc("baas")

    svc.retire_superseded_bot("BOT-old", operator="op1")

    baas.destroy_bot.assert_called_once()
    ck = baas.destroy_bot.call_args
    assert ck.kwargs["bot_uuid"] == "BOT-old"
    assert ck.kwargs["operator"] == "op1"
    # request_id is deterministic per bot_uuid (idempotent redelivery)
    rid = ck.kwargs["request_id"]
    assert isinstance(rid, str) and 32 <= len(rid) <= 64
    baas.destroy_bot.reset_mock()
    svc.retire_superseded_bot("BOT-old", operator="op1")
    assert baas.destroy_bot.call_args.kwargs["request_id"] == rid


@pytest.mark.unit
def test_retire_superseded_bot_swallows_destroy_failure():
    svc, baas = _svc("baas")
    baas.destroy_bot.side_effect = RuntimeError("baas down")

    # Best-effort: must not raise into the deploy path.
    assert svc.retire_superseded_bot("BOT-old") is None
    baas.destroy_bot.assert_called_once()
