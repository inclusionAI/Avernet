"""Create-as-service coding intake: create-as-personal, then upgrade to service.

A coding create (``engine_properties`` present) is personal-only by the
engine-strategy combination gate. A surface that opts in via
``BotCreateContext.service_intake`` instead drives the product's 开启服务 flow:
the spec is translated to ``personal`` so the same gate passes, and the
created + owned bot is upgraded through the :class:`ServiceIntakeSeam`. The
flow keeps one cohesive concern here: translation + post-create conversion,
without weakening the gate for surfaces that do not opt in.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Protocol

from agentclaw.community.core.bot_management.create_context import BotCreateContext
from agentclaw.community.core.bot_management.errors import ServiceIntakeConversionError
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.create_flow import BotCreateSpec
    from agentclaw.community.core.bot_management.services.bot_service import BotService

logger = get_logger()


class ServiceIntakeSeam(Protocol):
    """Upgrade a freshly created bot to a service Bot, intake-style.

    The create flow drives the second half of a create-as-service request.
    The implementation must tolerate a replayed conversion (the completion
    poll is the retry surface — a poll after a succeeded upgrade is success,
    not a conflict) and raise only on real failure.
    """

    def convert(self, bot_id: str, *, actor_id: str, owner_id: str) -> None: ...


def prepare_service_intake(
    spec: "BotCreateSpec",
    context: BotCreateContext,
    seam: ServiceIntakeSeam | None,
) -> tuple["BotCreateSpec", bool]:
    """Translate a create-as-service coding request to its personal-create form.

    Returns the spec to create with (``personal`` when intake, unchanged
    otherwise) and whether the post-create service upgrade should run. Keeps
    one cohesive concern in this module: the engine-strategy combination gate
    stays personal-only for template carries; an opted-in surface (see
    ``BotCreateContext.service_intake``) gets its service bot by translating
    the create and then upgrading, never by weakening the gate.
    """
    if not (
        context.service_intake
        and seam is not None
        and spec.bot_type == "service"
        and spec.engine_properties
    ):
        return spec, False
    return replace(spec, bot_type="personal"), True


def finish_service_intake(
    *,
    bot_id: str,
    user_id: str,
    bot_service: "BotService",
    seam: ServiceIntakeSeam,
) -> dict[str, Any]:
    """Drive the post-create upgrade and re-read the upgraded row.

    The bot already exists and is owned at this point, so a failed upgrade
    must not be swallowed: raise :class:`ServiceIntakeConversionError` naming
    the bot so the caller can retry the upgrade (the bot list shows the
    personal bot meanwhile) instead of re-creating.
    """
    try:
        seam.convert(bot_id, actor_id=user_id, owner_id=user_id)
    except Exception as exc:
        logger.error(
            "[create_flow.service_intake] conversion failed: bot_id=%s, "
            "error=%s",
            bot_id,
            exc,
            exc_info=True,
        )
        raise ServiceIntakeConversionError(
            f"bot {bot_id} was created as personal but its service conversion "
            f"failed; retry the lifecycle upgrade for this bot"
        ) from exc
    return bot_service.get_bot(bot_id, user_id)
