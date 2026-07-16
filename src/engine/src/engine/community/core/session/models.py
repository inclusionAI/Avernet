"""Wire types for the Session plugin boundary.

`Session` / `Message` are the read models the plugin returns; `Session*Request`
classes are the inputs every Session Protocol method accepts. These classes live
here (not in `api.session`, which has been removed) so engine implementations
and HTTP routers share one authoritative definition — see the `SessionService`
Protocol in `engine.community.core.session.protocol` for how they compose.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class Session(BaseModel):
    id: str
    key: str
    agent_id: str | None = None
    user_id: str
    title: str | None = None
    status: Literal["active", "archived", "closed"] = "active"
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    model: str | None = None
    runtime: str | None = None
    # 工作目录（aicoding 引擎使用；openclaw 引擎不暴露此概念，保持 None）。
    # source-of-truth 在 engine 自己（aicoding 在 relay binding.cwd），
    # OCB 后端不持久化，每次 list 实时透传。
    cwd: str | None = None
    permission_mode: str | None = None
    message_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    last_message: "Message | None" = None
    # 扩展信息字典，各引擎按需填充。
    # 各引擎按需填充工作项关联字段。
    ext_info: dict[str, Any] | None = None


class Message(BaseModel):
    id: str
    session_id: str
    role: Literal["user", "assistant", "system", "tool_use", "tool_result"]
    content: str
    created_at: datetime
    metadata: dict[str, Any] | None = None
    history_meta: dict[str, Any] | None = None


class SessionListRequest(BaseModel):
    user_id: str | None = None
    agent_id: str | None = None
    session_key: str | None = None
    status: Literal["active", "archived", "all"] = "active"
    limit: int = 20
    offset: int = 0


class SessionCreateRequest(BaseModel):
    title: str | None = None
    user_id: str = "default"
    agent_id: str | None = None
    model: str | None = None
    runtime: str | None = None
    cwd: str | None = None
    uuid: str | None = None
    extInfo: dict[str, Any] | None = None


class SessionDeleteRequest(BaseModel):
    session_id: str
    force: bool = False


class SessionClearRequest(BaseModel):
    session_id: str
    keep_system: bool = False


class SessionUpdateRequest(BaseModel):
    session_id: str
    title: str | None = None
    model: str | None = None
    runtime: str | None = None
    cwd: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    permission_mode: str | None = None


class SessionHistoryRequest(BaseModel):
    session_id: str
    limit: int | None = None
    offset: int = 0


class SessionResetRequest(BaseModel):
    """Inputs for SessionService.reset — matches OpenClaw's sessions.reset RPC."""

    session_key: str


class SessionHistoryResult(BaseModel):
    messages: list[Message]
    total: int | None = None


class SessionResetResult(BaseModel):
    """Outcome of SessionService.reset.

    `ok=True`: the plugin reached its engine; `payload` carries the engine-
    specific success body the server relays to the frontend.

    `ok=False`: the plugin could not carry out the reset; `error_code` /
    `error_message` describe the failure in the same vocabulary as the inbound
    wire error shape. Kept as primitive fields to avoid dragging `ErrorShape`
    (a dataclass from kernel/frames.py) into this pydantic module.
    """

    ok: bool
    payload: dict[str, Any] = {}
    error_code: str | None = None
    error_message: str | None = None


__all__ = [
    "Session",
    "Message",
    "SessionListRequest",
    "SessionCreateRequest",
    "SessionDeleteRequest",
    "SessionClearRequest",
    "SessionUpdateRequest",
    "SessionHistoryRequest",
    "SessionHistoryResult",
    "SessionResetRequest",
    "SessionResetResult",
]
