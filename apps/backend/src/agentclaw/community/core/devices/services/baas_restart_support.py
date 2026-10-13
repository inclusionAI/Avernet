"""Focused helpers for guarded BaaS restart task handling."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agentclaw.community.core.bot_management.engines import resolve_provisioning
from agentclaw.community.log import get_logger


logger = get_logger()

RESTART_IMAGE_POLICY_ON_SUCCESS_KEY = "restart_image_policy_on_success"
RESTART_REQUEST_ID_KEY = "restart_request_id"
RESTART_WORKFLOW_BASELINE_KEY = "restart_workflow_baseline"


def read_bot_ext_snapshot(
    bot_repository: Any,
    *,
    bot_id: str,
    owner_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    """Read the logical Bot ext value used by the restart transaction CAS."""
    if bot_repository is None:
        return None
    getter = getattr(bot_repository, "get_by_id_and_owner", None)
    if getter is None:
        return None
    try:
        bot = getter(bot_id, owner_id)
    except Exception as exc:
        logger.warning(
            "failed to read bot ext for bot_id=%s owner_id=%s: %s",
            bot_id,
            owner_id,
            exc,
        )
        return None
    ext = (bot or {}).get("ext") if isinstance(bot, dict) else None
    return (dict(ext) if isinstance(ext, dict) else {}, ext)


def resolve_restart_image_policy(
    *,
    binding: Any,
    publish_id: int,
    payload_value: str | None,
    request_id: str | None,
) -> str | None:
    """Read image policy from the current intent with old-task compatibility."""
    props = getattr(binding, "device_props", None) or {}
    request_matches = (
        request_id is not None and props.get(RESTART_REQUEST_ID_KEY) == request_id
    )
    publish_matches = str(props.get("restart_publish_id") or "") == str(publish_id)
    if (request_matches or publish_matches) and (
        RESTART_IMAGE_POLICY_ON_SUCCESS_KEY in props
    ):
        value = props.get(RESTART_IMAGE_POLICY_ON_SUCCESS_KEY)
        return value if isinstance(value, str) else None
    return payload_value


def read_restart_codefuse_token(
    *,
    bot_id: str,
    bot: Any,
    template_service: Any,
) -> str | None:
    """Read the decrypted runtime token only for engines that consume it."""
    if not isinstance(bot, dict):
        return None
    base_ctx, strategy = resolve_provisioning(
        bot_id=bot_id,
        owner_id=bot.get("owner_id") or "",
        active_engine=bot.get("active_engine"),
        bot_type=bot.get("bot_type") or "",
        template_type=bot.get("template_type"),
        template_config=None,
    )
    if not strategy.should_encrypt_template_token(base_ctx):
        return None
    if template_service is None:
        return None
    try:
        template_config = template_service.get_template_config(bot_id)
    except Exception as exc:
        logger.warning(
            "failed to reload template config for bot_id=%s: %s",
            bot_id,
            exc,
        )
        return None
    if not isinstance(template_config, dict):
        return None
    ctx = replace(base_ctx, template_config=template_config)
    return strategy.extract_runtime_token(ctx)
