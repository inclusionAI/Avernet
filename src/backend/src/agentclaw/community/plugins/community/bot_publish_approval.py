"""Community ``BotPublishApprovalPlugin`` — publish directly, no approval.

A real, deployable impl (not a ``MockSeam`` test double). The community build has
no approval workflow, so the publish strategy short-circuits to the same
direct-publish path the service uses for ``permission_owner == "caller"`` — the
bot reaches its published state without contacting any approval service.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.bot_publish_approval import (
    BotPublishApprovalPlugin,
    BotPublishCallbacks,
)

if TYPE_CHECKING:
    from agentclaw.community.core.operator_context import OperatorContext

logger = get_logger()


class DirectPublishApproval(BotPublishApprovalPlugin):
    """Community profile: publish directly, no approval workflow."""

    def publish(
        self,
        *,
        bot: Dict[str, Any],
        ext: Dict[str, Any],
        bot_id: str,
        owner_id: str,
        operator_id: str,
        operator: Optional["OperatorContext"],
        public: str,
        permission_owner: str,
        friend_approval: str,
        access_mode: str,
        callbacks: BotPublishCallbacks,
    ) -> Dict[str, Any]:
        logger.info(
            "[DirectPublishApproval] bot=%s owner=%s — no approval workflow in "
            "community, publishing directly",
            bot_id, owner_id,
        )
        return callbacks.publish_directly(
            bot=bot,
            ext=ext,
            bot_id=bot_id,
            owner_id=owner_id,
            operator_id=operator_id,
            public=public,
            permission_owner=permission_owner,
            friend_approval=friend_approval,
            access_mode=access_mode,
        )
