"""BCN V3 帧与 run 状态的最小实现。

只覆盖玩家 Bot 用到的请求/响应/事件，不引入 BCS 的 Rust 类型。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from time import time

PROTOCOL_VERSION = 3

INVALID_REQUEST = "invalid_request"
UNAUTHORIZED = "unauthorized"
NOT_FOUND = "not_found"
RUN_NOT_FOUND = "RUN_NOT_FOUND"
SCOPE_MISMATCH = "SCOPE_MISMATCH"
RUN_NOT_ABORTABLE = "RUN_NOT_ABORTABLE"
UNKNOWN_METHOD = "unknown_method"
ALREADY_CONNECTED = "already_connected"


def now_ms() -> int:
    return int(time() * 1000)


def request_frame(frame_id: str, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
    frame: dict[str, object] = {"type": "req", "id": frame_id, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def ok_response(frame_id: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    return {"type": "res", "id": frame_id, "ok": True, "payload": payload or {}}


def error_response(
    frame_id: str,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    retry_after_ms: int | None = None,
) -> dict[str, object]:
    return {
        "type": "res",
        "id": frame_id,
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "retry_after_ms": retry_after_ms,
        },
    }


def chat_event_frame(
    *,
    run_id: str,
    session_id: str,
    seq: int,
    state: str,
    content: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, object]:
    """构造 V3 canonical chat 事件；外层 seq 必须与 payload 内一致。"""

    payload: dict[str, object] = {
        "runId": run_id,
        "sessionId": session_id,
        "seq": seq,
        "ts": now_ms(),
        "state": state,
    }
    if content is not None:
        payload["content"] = content
    if error_code is not None:
        payload["errorCode"] = error_code
    if error_message is not None:
        payload["errorMessage"] = error_message
    if state == "final":
        payload["stopReason"] = "complete"
    return {"type": "event", "event": "chat", "payload": payload, "seq": seq}


def decode_frame(raw: str | bytes) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise ProtocolError("frame is not valid JSON") from error
    if not isinstance(value, dict):
        raise ProtocolError("frame must be a JSON object")
    return value


class ProtocolError(ValueError):
    """帧不满足 Bot 协议。"""


def extract_message_text(message: object) -> str:
    """从 ``message.content`` 中拼接文本块；兼容字符串与块数组。"""

    if not isinstance(message, dict):
        raise ProtocolError("chat.send params.message must be an object")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)
    raise ProtocolError("chat.send message.content must be a string or text block list")


@dataclass(frozen=True)
class ChatSend:
    """一条需要回复的 chat.send。"""

    frame_id: str
    session_key: str
    session_id: str
    text: str
    timeout_ms: int | None
    received_at_ms: int
    session_context: dict[str, object] = field(default_factory=dict)


def parse_chat_send(frame: dict[str, object]) -> ChatSend:
    if frame.get("type") != "req" or frame.get("method") != "chat.send":
        raise ProtocolError("not a chat.send frame")
    frame_id = frame.get("id")
    if not isinstance(frame_id, str) or not frame_id:
        raise ProtocolError("chat.send id must be a non-empty string")
    params = frame.get("params")
    if not isinstance(params, dict):
        raise ProtocolError("chat.send params must be an object")
    session_key = params.get("session_key")
    if not isinstance(session_key, str) or not session_key:
        raise ProtocolError("chat.send session_key must be a non-empty string")
    session_id = params.get("bcs_session_id")
    if not isinstance(session_id, str) or not session_id:
        # V3 不允许根据 bcs_group_id 自行重建 session。
        raise ProtocolError("chat.send bcs_session_id is required for protocol v3")
    text = extract_message_text(params.get("message"))
    if not text.strip():
        raise ProtocolError("chat.send message has no text content")
    timeout_ms = params.get("timeout_ms")
    if timeout_ms is not None:
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
            raise ProtocolError("chat.send timeout_ms must be a positive integer")
    session_context = params.get("session_context")
    return ChatSend(
        frame_id=frame_id,
        session_key=session_key,
        session_id=session_id,
        text=text,
        timeout_ms=timeout_ms,
        received_at_ms=now_ms(),
        session_context=session_context if isinstance(session_context, dict) else {},
    )


@dataclass
class Run:
    """一个 run 的本地状态；只接受自己的 run/session 组合。"""

    run_id: str
    session_key: str
    session_id: str
    deadline_ms: int | None
    chat: ChatSend
    seq: int = 0
    terminal: bool = False
    aborted: bool = False
    result: str | None = None
    final_sent: bool = False
    terminal_event_sent: bool = False
    started_at_ms: int = field(default_factory=now_ms)
    task: asyncio.Task[None] | None = field(default=None, repr=False, compare=False)
    terminal_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    abort_future: asyncio.Future[dict[str, object]] | None = field(
        default=None, repr=False, compare=False
    )

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def expired(self, at_ms: int | None = None) -> bool:
        if self.deadline_ms is None:
            return False
        return (now_ms() if at_ms is None else at_ms) > self.deadline_ms


class RunRegistry:
    """保存活跃 run 与终态墓碑，防止迟到结果污染后续节点。"""

    def __init__(self, tombstone_limit: int = 2048) -> None:
        self._runs: dict[str, Run] = {}
        self._tombstones: dict[str, str] = {}
        self._tombstone_limit = tombstone_limit

    def add(self, run: Run) -> bool:
        if run.run_id in self._runs or run.run_id in self._tombstones:
            return False
        self._runs[run.run_id] = run
        return True

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def active(self) -> list[Run]:
        return [run for run in self._runs.values() if not run.terminal]

    def mark_terminal(self, run: Run, *, result: str | None = None, aborted: bool = False) -> None:
        run.terminal = True
        run.aborted = aborted
        run.result = result
        self._runs.pop(run.run_id, None)
        self._tombstones[run.run_id] = run.session_key
        if len(self._tombstones) > self._tombstone_limit:
            self._tombstones.pop(next(iter(self._tombstones)))

    def tombstone_session(self, run_id: str) -> str | None:
        return self._tombstones.get(run_id)

    def session_id_for(self, session_key: str) -> str | None:
        for run in self._runs.values():
            if run.session_key == session_key:
                return run.session_id
        return None
