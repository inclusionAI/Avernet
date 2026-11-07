"""Shared publication errors and policy labels."""
from typing import Dict, Any
from agentclaw.community.plugin_api.approval_workflow import NO_WORKFLOW_MARKER
from agentclaw.community.log import get_logger

logger = get_logger()


def _resolve_access_mode(public: str, friend_approval: str) -> str:
    """Map bot visibility to passport accessMode."""
    if public == "1" and friend_approval == "0":
        return "OPEN"
    return "RESTRICTED"


def _is_workflow_unavailable(result: Dict[str, Any]) -> bool:
    """True when ``start_approval`` returned "no approval capability" rather
    than a real rejection. No-workflow impls prepend ``NO_WORKFLOW_MARKER`` to
    ``error_msg`` (see ``plugin_api/approval_workflow.py``); callers must treat
    this as a fall-through to direct publish, not a failure."""
    return (result.get("error_msg") or "").startswith(NO_WORKFLOW_MARKER)


# BCS-publish destination label for the approval prompt, keyed by public_scope.
_BCS_PUBLISH_SCENE: dict[str, str] = {
    "user": "加好友场景",
    "agent": "群聊场景",
}

# visibility=private 直连路径下更新的 BCS 属性字段随 public_scope 联动:
# "user" 发布进用户可见域 (BCS user_visibility); "agent" 发布进 agent/群聊域 (BCS visibility)。
_BCS_VISIBILITY_FIELD_BY_SCOPE: dict[str, str] = {
    "user": "user_visibility",
    "agent": "visibility",
}

# 慢连路径(走工单)在 BCS friend_ext 下记录工单的 sub-key 随 public_scope 联动:
# user → public_user_approval; agent → public_agent_approval。内层 block 形状一致。
_BCS_APPROVAL_KEY_BY_SCOPE: dict[str, str] = {
    "user": "public_user_approval",
    "agent": "public_agent_approval",
}

# AGREE 回调时把 public_*_approval 子块里的 view_friend_deps 提到 friend_ext 顶层,
# 也随 public_scope 联动 key: user → view_scope_user_friend_deps;
# agent → view_scope_agent_friend_deps。
_BCS_VIEW_SCOPE_DEPS_KEY_BY_SCOPE: dict[str, str] = {
    "user": "view_scope_user_friend_deps",
    "agent": "view_scope_agent_friend_deps",
}


class BotNotPublicError(Exception):
    """Exception raised when bot is not public."""
    def __init__(self, bot_id: str, message: str = "Bot is not public"):
        self.bot_id = bot_id
        self.message = f"{message}: bot_id={bot_id}"
        super().__init__(self.message)


class BotPublicServiceError(Exception):
    """Bot public service error."""
    pass


class BotNotFoundError(BotPublicServiceError):
    """Bot not found error."""
    pass


class BotPermissionError(BotPublicServiceError):
    """Bot permission error - user is not the owner."""
    pass
