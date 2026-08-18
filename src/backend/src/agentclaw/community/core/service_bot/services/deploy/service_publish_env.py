"""Environment-variable contract for service-bot deploy operations.

Service-bot publish/restart/rollback paths provision containers through
``BotBuildService`` directly, so they must compose the same engine-owned
``extra_envs`` that personal create/restart paths get from BotService, and
forward the source bot's ``template_config`` sandbox overrides separately.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_management.engines import resolve_provisioning
from agentclaw.community.log import get_logger

from .service_skills_manifest import service_skills_env_from_ext

logger = get_logger()


def _engine_extra_envs_from_bot(bot: dict[str, Any]) -> dict[str, str]:
    """Build engine-owned extra envs from the live source-bot metadata.

    ``BotService.get_bot`` attaches ``template_config`` for bot detail reads; if a
    legacy caller only has ``template_type`` the aicoding/claude_code strategy can
    still emit the legacy BOT_TYPE env.  Fail soft to match the personal
    create/restart behavior: environment derivation must not block publishing.
    """
    try:
        template_config = bot.get("template_config")
        if not isinstance(template_config, dict):
            template_config = None

        bot_id = str(bot.get("bot_id") or "")
        owner_id = str(bot.get("owner_id") or bot.get("entity_id") or "")
        bot_type = str(bot.get("bot_type") or "service")
        active_engine = bot.get("active_engine") or bot.get("engine_type")
        template_type = bot.get("template_type")

        ctx, strategy = resolve_provisioning(
            bot_id=bot_id,
            owner_id=owner_id,
            bot_type=bot_type,
            active_engine=active_engine if isinstance(active_engine, str) else None,
            template_type=template_type if isinstance(template_type, str) else None,
            template_config=template_config,
        )
        envs = strategy.build_extra_envs(ctx)
        if envs:
            logger.info(
                "[service_publish_extra_envs] engine extra_envs resolved: bot_id=%s env_keys=%s",
                bot_id,
                sorted(envs.keys()),
            )
            return dict(envs)
    except Exception as exc:
        logger.warning(
            "[service_publish_extra_envs] failed to build engine extra_envs: bot_id=%s error=%s",
            bot.get("bot_id"),
            exc,
        )
    return {}


def service_publish_template_config(bot: dict[str, Any]) -> dict[str, Any] | None:
    """Return source-bot template_config for BaaS sandbox overrides.

    Keep this separate from ``extra_envs`` to match personal bot create/restart:
    BaaS merges ``template_config.envs`` after ``extra_envs`` and also consumes
    image/resource_spec overrides at the sandbox edge.
    """
    template_config = bot.get("template_config")
    if not isinstance(template_config, dict) or not template_config:
        return None
    return dict(template_config)


def service_publish_extra_envs(
    ext: dict[str, Any] | None,
    bot: dict[str, Any],
) -> dict[str, str]:
    """Compose service publish envs for create/upgrade/restart/rollback.

    The immutable service Skills layout remains present for every service
    deploy.  Engine-owned envs (BOT_TYPE, AIX_DEVFLOW_INFO, GIT_ADDRESSES,
    RELAY_DEFAULT_*) are layered on top so service-bot pre-publish uses the same
    AIX/coding runtime contract as normal personal bot create/restart.
    """
    envs = dict(service_skills_env_from_ext(ext, bot))
    envs.update(_engine_extra_envs_from_bot(bot))
    return envs


__all__ = ["service_publish_extra_envs", "service_publish_template_config"]
