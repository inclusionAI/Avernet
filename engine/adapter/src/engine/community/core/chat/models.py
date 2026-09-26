"""
Chat request models — minimal surface after the M0 revision.

The old 灵矽AgentAPI response-side types (ChatStreamEvent / ContentUnit /
ChatResponseData / ChatCompleteResponse / …) were removed because no
production code ever consumed them. `ChatService.stream` now yields
`engine.community.kernel.frames.EventFrame` directly, matching the wire shape that the
OpenClaw engine already pushes in production.

What stays here:
  - `ChatRequest` — the request model for `ChatService.stream(request)`
  - `AttachFile` / `RuntimeTool` — referenced by ChatRequest
  - `TokenUsage` / `ToolCall` — utility types referenced by intent-eval and
    future plugins
  - `ErrorCode` / `ExtraParams` — platform-standard string constants
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from engine.community.kernel.frames import ErrorShape, EventFrame


class AttachFile(BaseModel):
    """附件文件"""

    fileType: str
    fileId: str | None = None
    fileUrl: str | None = None
    fileName: str | None = None
    contentId: str | None = None


class RuntimeTool(BaseModel):
    """运行时动态工具配置"""

    type: Literal["flow", "plugin", "agent"]
    name: str
    params: dict[str, str] | None = None
    expected: bool = False


class ChatRequest(BaseModel):
    """Chat request — 兼容灵矽平台标准请求格式。"""

    userId: str
    agentId: str
    query: str
    sessionId: str | None = None
    chatId: str | None = None
    reqType: Literal["agent", "task"] = "agent"
    aliveTime: int | None = None
    version: str | None = None
    store: bool = False
    ctxParams: dict[str, str] | None = None
    customParams: dict[str, dict[str, str]] | None = None
    attachFiles: list[AttachFile] | None = None
    tools: list[RuntimeTool] | None = None
    extraParams: dict | None = None


@dataclass
class ChatAbortRequest:
    """Inputs for `ChatService.abort` — matches the OpenClaw chat.abort RPC."""

    session_key: str
    run_id: str | None = None


@dataclass
class ChatAbortResult:
    """Outcome of `ChatService.abort`.

    `ok=True`: the plugin successfully talked to its engine. `aborted` reports
    whether the engine actually found and cancelled a run, and `run_id` echoes
    the gateway-resolved run id.

    `ok=False`: the plugin could not carry out the operation; `error` carries
    the standard wire error shape the server forwards to the client.

    `emit_events` is a list of `EventFrame`s the server should relay to the
    frontend after sending the response frame. OpenClaw uses this to synthesize
    a `chat` event with `state=aborted` on successful abort; other engines may
    leave it empty.
    """

    ok: bool
    aborted: bool = False
    run_id: str | None = None
    emit_events: list["EventFrame"] = field(default_factory=list)
    error: "ErrorShape | None" = None


class TokenUsage(BaseModel):
    """Token 使用统计。"""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


class ToolCall(BaseModel):
    """工具调用信息。"""

    id: str
    name: str
    input: dict
    output: str | None = None
    status: Literal["pending", "running", "completed", "failed", "awaiting_approval"]


class ErrorCode:
    """灵矽平台标准错误码。"""

    SERVICE_NO_AUTH = "SERVICE_NO_AUTH"
    AGENT_NO_AUTH = "AGENT_NO_AUTH"
    AGENT_NOT_EXIST = "AGENT_NOT_EXIST"
    APP_LIMIT_EXCEEDED = "APP_LIMIT_EXCEEDED"
    GLOBAL_LIMIT_EXCEEDED = "GLOBAL_LIMIT_EXCEEDED"
    AGENT_LIMIT_EXCEEDED = "AGENT_LIMIT_EXCEEDED"
    AGENT_CAS_LIMIT_EXCEEDED = "AGENT_CAS_LIMIT_EXCEEDED"
    SERVICE_TIMEOUT = "SERVICE_TIMEOUT"
    ILLEGAL_PARAMETER = "ILLEGAL_PARAMETER"


class ExtraParams:
    """extraParams 支持的参数键。"""

    AGENT_MODE = "agent.mode"
    AGENT_CHAT_MODE = "agent.chat.mode"
    SEC_CHECK_SKIP = "sec.check.skip"
    AGENT_RUNTIME_LOG_LEVEL = "agent.runtime.log.level"
    STREAM_CHUNK_SIZE = "stream.chunk.size"
    AGENT_SPLIT_REASONING_CONTENT = "agent.split.reasoning.content"
    NAMED_SPACE_CONTEXT_KEY = "named.space.context.key"


__all__ = [
    "AttachFile",
    "RuntimeTool",
    "ChatRequest",
    "TokenUsage",
    "ToolCall",
    "ErrorCode",
    "ExtraParams",
]
