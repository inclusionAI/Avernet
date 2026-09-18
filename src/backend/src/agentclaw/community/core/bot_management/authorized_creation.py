"""Authorized Bot creation with a durable retry-handle contract."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn

from agentclaw.community.core.bot_management.errors import BotCreationRetainedError
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotServiceError,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipError

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin


logger = get_logger()


def _record_owner_relationship(
    auth_rel_plugin: AuthRelationshipPlugin,
    *,
    user_id: str,
    agent_code: str,
    nick_name: str,
    bot_id: str,
) -> None:
    try:
        auth_result = auth_rel_plugin.create_relationship(
            work_no=user_id,
            agent_code=agent_code,
            description="Bot owner default authorization",
            operator_work_no=user_id,
            operator_name=nick_name,
        )
    except AuthRelationshipError:
        raise
    except Exception as exc:
        raise AuthRelationshipError(
            f"authorization relationship write failed for bot {bot_id}: {exc}"
        ) from exc
    if auth_result is None:
        raise AuthRelationshipError(
            f"authorization relationship write failed for bot {bot_id}"
        )
    logger.info(
        "[create_flow] Created owner auth relationship: bot_id=%s owner=%s "
        "agent_code=%s auth_id=%s",
        bot_id,
        user_id,
        agent_code,
        auth_result.get("auth_id"),
    )


def _raise_with_retry_handle(
    *,
    bot_service: BotService,
    bot_id: str,
    user_id: str,
    error: Exception,
    creation_succeeded: bool,
) -> NoReturn:
    if not creation_succeeded:
        try:
            retained_bot = bot_service.get_bot(bot_id, user_id)
        except (BotNotFoundError, BotServiceError):
            raise error
        if (
            not isinstance(retained_bot, dict)
            or retained_bot.get("bot_id") != bot_id
            or retained_bot.get("status") not in {"PENDING", "PROVISIONING"}
        ):
            raise error
    raise BotCreationRetainedError(bot_id=bot_id, message=str(error)) from error


def create_authorized_bot(
    *,
    bot_service: BotService,
    auth_rel_plugin: AuthRelationshipPlugin,
    user_id: str,
    nick_name: str,
    bot_id: str,
    agent_code: str,
    create_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Create the Bot and relationship while preserving a durable retry ID."""
    try:
        result = bot_service.create_bot(**create_kwargs)
    except BotServiceError as exc:
        _raise_with_retry_handle(
            bot_service=bot_service,
            bot_id=bot_id,
            user_id=user_id,
            error=exc,
            creation_succeeded=False,
        )
    try:
        _record_owner_relationship(
            auth_rel_plugin,
            user_id=user_id,
            agent_code=agent_code,
            nick_name=nick_name,
            bot_id=bot_id,
        )
    except AuthRelationshipError as exc:
        _raise_with_retry_handle(
            bot_service=bot_service,
            bot_id=bot_id,
            user_id=user_id,
            error=exc,
            creation_succeeded=True,
        )
    return result
