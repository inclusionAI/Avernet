"""Caller-container provisioning support split out of ExpertChatInstanceService.

Holds the two self-contained pieces of the per-caller container lifecycle
that grew the service past the 1000-line architecture gate
(``test_no_oversized_modules``):

- ``assemble_container_publish_env`` — the Step 3/4 publish environment
  assembly shared verbatim by the fresh-release and upgrade paths;
- ``exchange_caller_identity`` — the post-SUCCESS caller identity
  exchange (failures are logged, never interrupt container startup).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Tuple

from agentclaw.community.core.caller_identity.contracts import CallerIdentityStage
from agentclaw.community.core.caller_identity.protocols import (
    CallerIdentityTokenExchangeProtocol,
    CallerRuntimeUpdaterProtocol,
    CallerTokenProviderProtocol,
)
from agentclaw.community.core.service_bot.services.deploy.service_publish_env import (
    service_publish_extra_envs,
    service_publish_template_config,
)
from agentclaw.community.log import get_logger

logger = get_logger()


def assemble_container_publish_env(
    bot_info: Dict[str, Any], publish_ext: Dict[str, Any],
) -> Tuple[Dict[str, str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Compose the BaaS payload environment contract for a caller container.

    Both the fresh-release (Step 3) and the recycled-upgrade (Step 4) path
    forward the same environment as the service-bot publish/restart/rollback
    path — ``bot_info`` must already be loaded through ``BotService.get_bot``
    so the frozen ``template_config`` snapshot (``ac_templates.ext``) is
    attached: without it the aicoding strategy's ``build_extra_envs`` emits
    no ``GIT_ADDRESSES`` / ``AIX_DEVFLOW_INFO`` / ``RELAY_DEFAULT_*``, and
    the caller container silently loses the engine-owned envs.

    Returns ``(extra_envs, template_config, ext_info)``:
    ``extra_envs`` — engine-owned env vars; ``template_config`` — the
    sandbox template overrides BaaS layers on top; ``ext_info`` — the
    skills-manifest carrying dict, or ``None`` when the publish ext did not
    include one.
    """
    skills_env = service_publish_extra_envs(publish_ext, bot_info)
    sandbox_template_config = service_publish_template_config(bot_info)
    ext_info = (
        {"skills_manifest": publish_ext["skills_manifest"]}
        if publish_ext.get("skills_manifest") is not None
        else None
    )
    return skills_env, sandbox_template_config, ext_info


async def exchange_caller_identity(
    *,
    caller_identity: CallerIdentityTokenExchangeProtocol,
    token_provider: CallerTokenProviderProtocol,
    runtime_updater: CallerRuntimeUpdaterProtocol,
    iam_token: str,
    user_id: str,
    bot_id: str,
    owner_id: str,
    publish_id: int,
    binding_id: Optional[int] = None,
    force_runtime_refresh: bool = False,
) -> None:
    """Exchange and install Caller credential for the container.

    Called after the container is successfully pulled (status == "SUCCESS").
    Failures are logged but do not interrupt the container startup flow.

    Args:
        caller_identity: The caller identity exchange protocol implementation.
        token_provider: Token provider used by the exchange.
        runtime_updater: Runtime updater used by the exchange.
        iam_token: IAM token for authentication.
        user_id: The caller's user ID.
        bot_id: The bot ID.
        owner_id: The bot owner's ID.
        publish_id: The service bot publish record ID.
        binding_id: The device binding ID (optional).
        force_runtime_refresh: Reinstall after provisioning even if expiry is not later.
    """
    exchange_kwargs = {"force_runtime_refresh": True} if force_runtime_refresh else {}
    logger.info(
        "[ExpertChatInstance] Caller identity exchange requested: bot=%s owner=%s user=%s binding_id=%s force_runtime_refresh=%s",
        bot_id, owner_id, user_id, binding_id, force_runtime_refresh,
    )
    try:
        await asyncio.to_thread(
            caller_identity.exchange_caller_identity,
            iam_token=iam_token,
            caller_user_id=user_id,
            bot_id=bot_id,
            owner_user_id=owner_id,
            token_provider=token_provider,
            runtime_updater=runtime_updater,
            stage=CallerIdentityStage.ONLINE.value,
            publish_id=publish_id,
            entity_id=owner_id,
            binding_id=binding_id,
            is_test_exchange=False,
            **exchange_kwargs,
        )
        logger.info(
            "[ExpertChatInstance] Caller identity exchange succeeded: bot=%s owner=%s user=%s binding_id=%s force_runtime_refresh=%s",
            bot_id, owner_id, user_id, binding_id, force_runtime_refresh,
        )
    except Exception as e:
        # Log error but do not interrupt container startup
        logger.error(
            "[ExpertChatInstance] Caller identity exchange failed: bot=%s owner=%s user=%s binding_id=%s force_runtime_refresh=%s error_type=%s",
            bot_id, owner_id, user_id, binding_id, force_runtime_refresh, type(e).__name__,
        )
