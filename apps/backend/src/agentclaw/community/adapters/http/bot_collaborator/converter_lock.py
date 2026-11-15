"""Shared HTTP conversions for collaborator locks."""

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.bot_collaborator.schemas import LockInfo


def _get_user_id(user: AuthenticatedUser) -> str | None:
    """获取用户ID，匿名用户返回None。"""
    user_id = user.staffId
    return user_id if user_id and user_id != "anonymous" else None


def _lock_info(lock, holder_name: str | None = None) -> LockInfo:
    """构建LockInfo对象。"""
    return LockInfo(
        id=lock.id,
        lock_key=lock.lock_key,
        holder_user_id=lock.holder_user_id,
        holder_name=holder_name,
        gmt_create=lock.gmt_create,
    )


