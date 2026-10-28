"""审批回调处理逻辑

The single caller (the approval HTTP router's ``approval_callback``)
resolves ``BotPublicService`` via FastAPI ``Injected`` and passes it in,
so this module no longer needs to invoke the service-locator.
"""
from typing import Dict, Any
import re

from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.env_utils import get_current_env

from agentclaw.community.core.bot_public.services.bot_public_service import BotPublicService
from agentclaw.community.log import get_logger

logger = get_logger()


def handle_approval_callback(
    data: Dict[str, Any], bot_public_service: BotPublicService, risk_repository=None,
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
    # public_scope 区分新版发布(走 BCS, 值如 user/agent)与老版发布(写 ac_bots)；
    # 平台回传字符串，非空即新版、空即老版，原样透传给 service 分支。
    public_scope = (data.get("public_scope") or "").strip()

    logger.info(
        "[回调] global_unique_id=%s, last_operate=%s, public_scope=%s",
        global_unique_id, last_operate, public_scope or "(legacy)",
    )

    # 从 global_unique_id 提取 publish_id（取 _ 分隔的最后部分）
    if not global_unique_id:
        logger.error("[回调] global_unique_id 为空")
        return {"success": False, "message": "global_unique_id is empty"}

    risk_check_id = (data.get("risk_check_id") or "").strip()
    uuid_pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    match = re.fullmatch(r"antprocess-agentclaw_botpublic_risk-(" + uuid_pattern + r")(?:_.+)?", global_unique_id)
    puid_risk_id = match.group(1) if match else None
    if risk_check_id and not re.fullmatch(uuid_pattern, risk_check_id):
        return {"success": False, "message": "invalid risk callback identifier"}
    if risk_check_id and puid_risk_id and risk_check_id != puid_risk_id:
        return {"success": False, "message": "risk callback association mismatch"}
    risk_id = risk_check_id or puid_risk_id
    def invoke():
        risk_args = {"risk_check_id": risk_id} if risk_id else {}
        return bot_public_service.handle_public_approval_callback(
            bot_id=bot_id, owner_id=owner_id, puid=global_unique_id,
            last_operate=last_operate, public_scope=public_scope, **risk_args,
        )
    if not risk_id or risk_repository is None:
        return invoke()
    tenant = risk_repository.find_risk_tenant(env=get_current_env(), risk_check_id=risk_id)
    if not tenant:
        return {"success": False, "message": "risk callback association unavailable"}
    with avernet_tenant_scope(tenant):
        return invoke()
