"""Unit tests for the provider-behavior seam (Task 3).

The seam replaces the scattered ``== teclaw`` branches in the publish flow: each
provider-varying deploy-time step is a method on a ``ProviderBehavior`` selected
by ``device_provider`` via ``ProviderBehaviorRouter``.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.service_bot.services.deploy.teclaw_file_promotion import (
    PromotedRefs,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.services.publish_flow.provider_behavior import (
    DefaultProviderBehavior,
    ProviderBehaviorRouter,
    TeclawProviderBehavior,
)


def _teclaw_behavior(*, build_service=None, resolver=None, dispatcher=None, promotion=None):
    return TeclawProviderBehavior(
        build_service=build_service or Mock(),
        resolver=resolver or Mock(),
        device_fs_dispatcher=dispatcher or Mock(),
        teclaw_file_promotion=promotion or Mock(),
    )


# ── router resolution ───────────────────────────────────────────────────────

def test_router_resolves_teclaw_to_teclaw_behavior():
    teclaw = _teclaw_behavior()
    default = DefaultProviderBehavior()
    router = ProviderBehaviorRouter(
        {"teclaw": teclaw, "arca": default, "baas": default}, default_provider_key="baas"
    )
    assert router.resolve("teclaw") is teclaw
    assert router.resolve("arca") is default
    assert router.resolve("baas") is default


def test_router_unknown_or_none_falls_back_to_default():
    teclaw = _teclaw_behavior()
    default = DefaultProviderBehavior()
    router = ProviderBehaviorRouter(
        {"teclaw": teclaw, "baas": default}, default_provider_key="baas"
    )
    assert router.resolve("unknown-provider") is default
    assert router.resolve(None) is default


def test_router_requires_default_key_present():
    with pytest.raises(ValueError):
        ProviderBehaviorRouter({"teclaw": _teclaw_behavior()}, default_provider_key="baas")


# ── default behavior: the historical (ARCA/baas) semantics ──────────────────

@pytest.mark.asyncio
async def test_default_behavior_members():
    default = DefaultProviderBehavior()
    assert default.supports_scale is True
    assert default.destroys_verify_bot_on_online is True
    # no-ops (must not raise / touch anything)
    assert await default.stage_build_files(
        artifact=SimpleNamespace(ext={}), bot={}, bot_id="b", owner_id="u", publish_id=1
    ) is None
    assert default.refresh_after_upgrade(bot_uuid="BOT-x", bot={}) is None


def test_default_persist_stage_promotion_is_noop():
    # ARCA/baas keep no frozen artifact snapshot → ext is left untouched even when
    # a config_artifact + overrides are present.
    default = DefaultProviderBehavior()
    ext = {"config_artifact": {"engine_ext": {"stage": "draft"}}}
    default.persist_stage_promotion(
        ext=ext, stage=PublishStage.VERIFY, engine_overrides={"channels": {}}
    )
    assert ext["config_artifact"]["engine_ext"]["stage"] == "draft"  # not restamped
    assert "engine_overrides_by_stage" not in ext  # not stored


# ── teclaw behavior: the provider-specific steps ────────────────────────────

def test_teclaw_behavior_flags():
    teclaw = _teclaw_behavior()
    assert teclaw.supports_scale is False
    assert teclaw.destroys_verify_bot_on_online is False


def test_teclaw_persist_stage_promotion_stamps_and_stores():
    # teclaw stamps the promoted stage into the stored config_artifact snapshot and
    # stores this stage's channel overrides for restart/redeliver reproduction.
    teclaw = _teclaw_behavior()
    channels = {"channels": {"dingding": {"enabled": True}}}
    ext = {"config_artifact": {"engine_ext": {"stage": "draft"}}}
    teclaw.persist_stage_promotion(
        ext=ext, stage=PublishStage.ONLINE, engine_overrides=channels
    )
    assert ext["config_artifact"]["engine_ext"]["stage"] == "release"
    assert ext["engine_overrides_by_stage"]["online"] == channels


def test_teclaw_persist_stage_promotion_noops_missing_pieces():
    # No config_artifact → nothing to stamp; None overrides → nothing to store.
    teclaw = _teclaw_behavior()
    ext: dict = {}
    teclaw.persist_stage_promotion(
        ext=ext, stage=PublishStage.VERIFY, engine_overrides=None
    )
    assert ext == {}


def test_teclaw_refresh_after_upgrade_calls_build_service():
    build_service = Mock()
    teclaw = _teclaw_behavior(build_service=build_service)
    bot = {"bot_id": "b2"}
    teclaw.refresh_after_upgrade(bot_uuid="BOT-old", bot=bot)
    build_service.refresh_teclaw_mcp_outbound_rule.assert_called_once_with(
        bot_uuid="BOT-old", bot=bot
    )


@pytest.mark.asyncio
async def test_teclaw_stage_build_files_merges_refs():
    promotion = Mock()
    promotion.stage_files = AsyncMock(return_value=PromotedRefs(
        resources=[{"name": "a.csv", "store": "bot-data",
                    "path": "staff_u/b_5_verify/teclaw/workspace/a.csv"}],
        identity_files=[{"name": "MEMORY.md", "store": "bot-data",
                         "path": "staff_u/b_5_verify/teclaw/identity/MEMORY.md"}],
    ))
    resolver = Mock()
    dispatcher = Mock()
    teclaw = _teclaw_behavior(resolver=resolver, dispatcher=dispatcher, promotion=promotion)
    artifact = SimpleNamespace(
        ext={"config_artifact": {"resources": [], "identity_files": []}}
    )

    await teclaw.stage_build_files(
        artifact=artifact, bot={"entity_type": "staff", "entity_id": "u"},
        bot_id="b", owner_id="u", publish_id=5,
    )

    ca = artifact.ext["config_artifact"]
    assert ca["resources"] == [
        {"name": "a.csv", "store": "bot-data",
         "path": "staff_u/b_5_verify/teclaw/workspace/a.csv"},
    ]
    assert ca["identity_files"][0]["name"] == "MEMORY.md"
    kwargs = promotion.stage_files.call_args.kwargs
    assert kwargs["bot_id"] == "b"
    assert kwargs["publish_id"] == 5
    assert kwargs["stage"] == "verify"
    assert kwargs["device_fs"] is dispatcher.dispatch.return_value
    resolver.resolve_for_bot.assert_called_once_with("b", "u")


@pytest.mark.asyncio
async def test_teclaw_stage_build_files_raises_without_config_artifact():
    teclaw = _teclaw_behavior()
    artifact = SimpleNamespace(ext={})  # no config_artifact dict
    with pytest.raises(PublishFlowServiceError):
        await teclaw.stage_build_files(
            artifact=artifact, bot={}, bot_id="b", owner_id="u", publish_id=5
        )


@pytest.mark.asyncio
async def test_default_draft_restore_delegates_to_migration_restore():
    default = DefaultProviderBehavior()
    build_service = Mock()
    build_service.restore_draft_async = AsyncMock(return_value={"restore_type": "migration_path"})
    ext = {"migration_path": "/artifact/v1"}

    assert default.validate_draft_restore_artifact(ext) is None
    result = await default.restore_draft(
        build_service=build_service,
        bot={"bot_id": "b"},
        bot_uuid="BOT-draft",
        owner_id="u",
        source_version=1,
        artifact_ext=ext,
    )

    assert result["restore_type"] == "migration_path"
    build_service.restore_draft_async.assert_awaited_once_with(
        bot={"bot_id": "b"}, source_version=1, artifact_ext=ext
    )


@pytest.mark.asyncio
async def test_teclaw_draft_restore_delegates_to_hot_update():
    build_service = Mock()
    build_service.restore_teclaw_draft_async = AsyncMock(
        return_value={"restore_type": "config_artifact"}
    )
    teclaw = _teclaw_behavior(build_service=build_service)
    ext = {"config_artifact": {"engine_type": "teclaw"}}

    assert teclaw.validate_draft_restore_artifact(ext) is None
    result = await teclaw.restore_draft(
        build_service=build_service,
        bot={"bot_id": "b"},
        bot_uuid="BOT-current",
        owner_id="u",
        source_version=1,
        artifact_ext=ext,
    )

    assert result["restore_type"] == "config_artifact"
    build_service.restore_teclaw_draft_async.assert_awaited_once_with(
        bot_uuid="BOT-current",
        bot={"bot_id": "b"},
        owner_id="u",
        source_version=1,
        artifact_ext=ext,
        baas_publish_id=None,
        request_id=None,
    )


def test_teclaw_draft_restore_rejects_non_teclaw_artifact():
    teclaw = _teclaw_behavior(build_service=Mock())

    reason = teclaw.validate_draft_restore_artifact(
        {"config_artifact": {"engine_type": "openclaw"}}
    )

    assert reason == "上一版本的 config_artifact 不是 teclaw 构造物"
