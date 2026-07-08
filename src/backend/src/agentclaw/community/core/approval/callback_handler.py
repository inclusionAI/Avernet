"""审批回调处理逻辑

The single caller (the approval HTTP router's ``approval_callback``)
resolves ``BotPublicService`` via FastAPI ``Injected`` and passes it in,
so this module no longer needs to invoke the service-locator.
"""
from typing import Dict, Any

from agentclaw.community.core.bot_public.services.bot_public_service import BotPublicService
from agentclaw.community.log import get_logger

logger = get_logger()


def handle_approval_callback(
    data: Dict[str, Any], bot_public_service: BotPublicService,
) -> Dict[str, Any]:
    """
    处理审批结果回调

    Args:
        data: 回调数据，包含 global_unique_id, operate, state 等
        bot_public_service: injected by the calling route

    Returns:
        {"success": bool, "message": str}
    """

    global_unique_id = data.get("global_unique_id") or ""
    last_operate = (data.get("last_operate") or "").upper()
    owner_id = data.get("owner_id") or ""
    bot_id = data.get("bot_id") or ""

    logger.info("[回调] global_unique_id=%s, last_operate=%s", global_unique_id, last_operate)

    # 从 global_unique_id 提取 publish_id（取 _ 分隔的最后部分）
    if not global_unique_id:
        logger.error("[回调] global_unique_id 为空")
        return {"success": False, "message": "global_unique_id is empty"}

    return bot_public_service.handle_public_approval_callback(
        bot_id=bot_id, owner_id=owner_id, puid=global_unique_id, last_operate=last_operate,
    )
