"""默认 StreamConverter 实现（BCN 协议）

由 SseConverterFactory 通过 DI 注入，按名称实例化。

转换逻辑:
  - chunk.type == "delta"     → SSE event: chat (增量文本)
  - chunk.type == "final"     → SSE event: chat (最终完整文本 + usage)
  - chunk.type == "error"     → SSE event: chat (错误信息)
  - chunk.type == "aborted"   → SSE event: chat (中止)
  - chunk.type == "agent"     → SSE event: agent (引擎事件)
  - chunk.type == "heartbeat" → SSE 注释帧: : heartbeat
  - chunk.type == "usage"     → 无独立事件（忽略）
"""

from __future__ import annotations

import json
import time
from typing import Any

from secbaas.community.api.sse import SseEvent, StreamChunk


class DefaultStreamConverter:
    """Convert Baas / engine stream chunks to the BCN downlink SSE protocol.

    Dispatch is driven by ``StreamChunk.type`` (delta / final / error / agent),
    not by an inner engine envelope:
    - ``delta``  -> chat delta, carrying only the incremental ``deltaText``
      (BCS self-accumulates by run_id; no ``message`` is sent).
    - ``final``  -> chat final terminal marker (no body; BCS flushes its
      accumulated buffer).
    - ``error``  -> chat error, carrying the error text.
    - ``agent``  -> agent event; ``metadata["engine_frame"]`` holds the raw
      engine payload (``stream`` + ``data``), handled as the payload directly.

    Every event's ``ts`` is stamped at conversion time (receipt order) rather
    than read from the engine frame, so downstream sequencing never reorders.
    """

    def __init__(self) -> None:
        self._seq = 0

    @staticmethod
    def name() -> str:
        return "default"

    def convert(self, chunk: StreamChunk, *, run_id: str) -> SseEvent | None:
        converted = _transform_chunk(chunk, _engine_name(chunk))
        if converted is None:
            return None
        if converted["event"].startswith(":"):
            return SseEvent(event=converted["event"], data="")
        return self._build_event(converted["event"], converted["data"], run_id)

    def _build_event(
        self,
        event: str,
        data: dict[str, Any],
        run_id: str,
    ) -> SseEvent:
        self._seq += 1
        payload = dict(data)
        payload.setdefault("runId", run_id)
        payload["seq"] = self._seq
        # Stamp ts at receipt time (ms). chat frames no longer carry a payload,
        # and using the engine's ts risks out-of-order timestamps across the
        # chat/agent interleave. seq already fixes ordering; ts is informational.
        payload.setdefault("ts", int(time.time() * 1000))
        return SseEvent(
            event=event,
            id=str(self._seq),
            data=json.dumps(payload, ensure_ascii=False),
        )


def _engine_name(chunk: StreamChunk) -> str:
    # Prefer the strongly-typed engine_type (set by ClawBotService via
    # `replace(chunk, engine_type=...)`); fall back to metadata for producers
    # that only tag the engine there.
    if chunk.engine_type:
        return str(chunk.engine_type)
    metadata = chunk.metadata or {}
    value = (
        metadata.get("engine")
        or metadata.get("engine_type")
        or metadata.get("engineType")
        or metadata.get("provider")
        or ""
    )
    return str(value)


def _agent_payload(chunk: StreamChunk) -> dict[str, Any]:
    """The raw engine payload for an agent chunk lives under
    ``metadata["engine_frame"]`` (stored directly, NOT wrapped in a
    type/event/payload envelope)."""
    metadata = chunk.metadata or {}
    payload = metadata.get("engine_frame")
    return payload if isinstance(payload, dict) else {}


def _transform_chunk(chunk: StreamChunk, engine: str) -> dict[str, Any] | None:
    """Route by StreamChunk.type. delta/final/error are chat events built from
    the chunk's own fields; agent events read the raw engine payload."""
    ctype = chunk.type

    if ctype == "delta":
        data: dict[str, Any] = {"state": "delta"}
        if chunk.content:
            data["deltaText"] = chunk.content
        return {"event": "chat", "data": data}

    if ctype == "final":
        # Rebuild `message` from the chunk's full text (content == the engine's
        # message.content[].text on the BaaS side). BCS's relay path reads
        # message.content[].text to decide relay + to route the final to the
        # group, so an empty final would be skipped. For persistence BCS is in
        # self-accumulate mode and flushes its own delta buffer, ignoring this
        # message (no duplication). Deltas still carry only deltaText.
        data: dict[str, Any] = {"state": "final"}
        if chunk.content:
            data["message"] = {
                "role": "assistant",
                "content": [{"type": "text", "text": chunk.content}],
                "timestamp": int(time.time() * 1000),
            }
        metadata = chunk.metadata or {}
        _copy_present(metadata, data, ("stopReason", "usage"))
        return {"event": "chat", "data": data}

    if ctype == "error":
        data = {"state": "error", "errorMessage": chunk.content or "Unknown error"}
        metadata = chunk.metadata or {}
        _copy_present(metadata, data, ("errorKind",))
        return {"event": "chat", "data": data}

    if ctype == "aborted":
        data = {"state": "aborted"}
        metadata = chunk.metadata or {}
        _copy_present(metadata, data, ("stopReason",))
        return {"event": "chat", "data": data}

    if ctype == "agent":
        return _transform_agent(_agent_payload(chunk), engine)

    if ctype == "heartbeat":
        return {"event": ": heartbeat", "data": {}}

    # usage / unknown: no standalone SSE event.
    return None


def _transform_agent(payload: dict[str, Any], engine: str) -> dict[str, Any] | None:
    """Build an agent SSE event from the raw engine payload (stream + data)."""
    stream = payload.get("stream")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    if stream == "thinking":
        out: dict[str, Any] = {"stream": "thinking"}
        _copy_present(data, out, ("delta", "text"))
        return {"event": "agent", "data": out}

    if stream == "tool":
        return _transform_tool(data, engine)

    if stream == "command_output":
        # Only claude_code relies on command_output for the tool result. Other
        # engines (e.g. openclaw) ALSO emit a `tool`/`phase:result` frame for
        # the same toolCallId, so consuming command_output there would persist a
        # duplicate, name-less tool_result. Ignore it for non-claude engines.
        if not _is_claude_engine(engine):
            return None
        return _transform_command_output(data)

    if stream == "lifecycle":
        phase = data.get("phase")
        if phase not in {"start", "end"}:
            return None
        out = {"stream": "lifecycle", "phase": phase}
        _copy_present(data, out, ("model", "agentMode"))
        return {"event": "agent", "data": out}

    return None


def _transform_command_output(data: dict[str, Any]) -> dict[str, Any] | None:
    if data.get("phase") != "end":
        return None
    out: dict[str, Any] = {"stream": "tool", "phase": "result"}
    _copy_present(data, out, ("toolCallId",))
    output = data.get("output")
    if output is not None:
        out["result"] = _claude_tool_result(output)
    exit_code = data.get("exitCode")
    out["isError"] = isinstance(exit_code, int) and exit_code != 0
    _copy_present(data, out, ("exitCode", "durationMs", "cwd"))
    return {"event": "agent", "data": out}


def _transform_tool(data: dict[str, Any], engine: str) -> dict[str, Any] | None:
    if _is_claude_tool(data, engine):
        return _transform_claude_tool(data)
    return _transform_openclaw_tool(data)


def _is_claude_engine(engine: str) -> bool:
    return engine.replace("-", "_").lower() in {"claude", "claude_code"}


def _is_claude_tool(data: dict[str, Any], engine: str) -> bool:
    if _is_claude_engine(engine):
        return True
    return data.get("type") in {"start", "update", "result"} and any(
        key in data for key in ("toolName", "input", "output")
    )


def _transform_claude_tool(data: dict[str, Any]) -> dict[str, Any] | None:
    phase = {"start": "start", "result": "result"}.get(data.get("type"))
    if phase is None:
        return None

    out: dict[str, Any] = {"stream": "tool", "phase": phase}
    if data.get("toolName"):
        out["name"] = data["toolName"]
    if data.get("toolCallId"):
        out["toolCallId"] = data["toolCallId"]
    if phase == "start" and data.get("input") is not None:
        out["args"] = data["input"]
    if phase == "result":
        output = data.get("output")
        if output is not None:
            out["result"] = _claude_tool_result(output)
        out["isError"] = bool(data.get("isError", False))
    return {"event": "agent", "data": out}


def _claude_tool_result(output: Any) -> dict[str, Any]:
    if isinstance(output, dict):
        return output
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def _transform_openclaw_tool(data: dict[str, Any]) -> dict[str, Any] | None:
    phase = data.get("phase")
    if phase not in {"start", "update", "result"}:
        return None

    out: dict[str, Any] = {"stream": "tool", "phase": phase}
    _copy_present(
        data,
        out,
        (
            "name",
            "toolCallId",
            "args",
            "result",
            "partialResult",
            "isError",
            "exitCode",
            "durationMs",
            "cwd",
        ),
    )
    return {"event": "agent", "data": out}


def _copy_present(
    source: dict[str, Any],
    target: dict[str, Any],
    keys: tuple[str, ...],
) -> None:
    for key in keys:
        if source.get(key) is not None:
            target[key] = source[key]
