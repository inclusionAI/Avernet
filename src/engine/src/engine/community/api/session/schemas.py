"""Session router HTTP schemas.

HTTP-only request bodies. Plugin-boundary types (`SessionListRequest`,
`SessionCreateRequest`, etc.) live in `engine.community.core.session.models` and are
imported directly by the router.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class CreateSessionBody(BaseModel):
    """创建会话请求体"""
    title: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    model: Optional[str] = None
    runtime: Optional[str] = None
    engine: Optional[str] = None
    uuid: Optional[str] = None
    extInfo: Optional[dict[str, Any]] = None
    payload: Optional[dict[str, Any]] = None


class UpdateSessionBody(BaseModel):
    """更新会话请求体"""
    title: Optional[str] = None
    model: Optional[str] = None
    runtime: Optional[str] = None
    cwd: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    permission_mode: Optional[str] = None
    # 引擎特定扩展字段（OSS 层不感知具体内部词汇）。
    ext_info: Optional[dict[str, Any]] = None
    engine: Optional[str] = None


__all__ = ["CreateSessionBody", "UpdateSessionBody"]
