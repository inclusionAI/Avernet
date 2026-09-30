"""LocalBotPublishApproval -- local-mode publish strategy: skip the
antprocess workflow and publish directly.

Antprocess isn't reachable on a dev laptop (MOSN/Layotto not running),
so the local strategy short-circuits to the same direct-publish path
the service already uses for ``permission_owner == "caller"``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.bot_publish_approval import (
    BotPublishApprovalPlugin,
    BotPublishCallbacks,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam

if TYPE_CHECKING:
    from agentclaw.community.core.operator_context import OperatorContext

logger = get_logger()


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="no I/O",
)
class LocalBotPublishApproval(MockSeam, BotPublishApprovalPlugin):
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
            "[LocalBotPublishApproval] bot=%s owner=%s — antprocess "
            "unavailable in local mode, publishing directly",
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
