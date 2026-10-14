"""A2A v1.0 wire conversion — kernel internal events ↔ standard StreamResponse.

Pure functions; no framework imports; no state.

Wire contract (verified in spike G1a with a2a-sdk 1.2.2):
  - StreamResponse.result has exactly one member: task | message |
    statusUpdate | artifactUpdate
  - seq/lastSeq/final live inside the member's metadata (NOT top-level —
    protobuf ParseDict rejects unknown fields)
  - final goes in metadata as string "true" (bool also works but "true" is
    safest for map<string,string> environments)
  - artifact chunks follow the stage table: (F,F)→first block, (T,F)→middle,
    (T,T)→final chunk, (F,T)→full-replacement final update
"""
from __future__ import annotations

from typing import Any

KINDS = ("task", "message", "statusUpdate", "artifactUpdate")
TERMINAL_STATES = (
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
)


def kernel_event_to_wire(event: dict[str, Any]) -> dict[str, Any]:
    """kernel event {"seq": int, "type": str, "ev": dict} → A2A wire event."""
    ev = event.get("ev") or {}
    seq = event.get("seq")
    out = dict(ev)

    for member in ("statusUpdate", "artifactUpdate"):
        if member in out and out[member] is not None:
            inner = dict(out[member])
            meta = dict(inner.get("metadata") or {})
            if seq is not None:
                meta["seq"] = seq
            inner["metadata"] = meta
            out[member] = inner

    return out


def wire_task_event(run_id: str, context_id: str, state: str) -> dict[str, Any]:
    return {"seq": None, "type": "task", "ev": {
        "task": {"id": run_id, "contextId": context_id,
                  "status": {"state": state}, "metadata": {}}}}


def wire_status_update(run_id: str, context_id: str, state: str, seq: int,
                       final: bool = False, last_seq: int | None = None,
                       extra_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"seq": seq}
    if final:
        meta["final"] = "true"
    if last_seq is not None:
        meta["lastSeq"] = last_seq
    if extra_metadata:
        meta.update(extra_metadata)
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": state}, "metadata": meta}}}


def wire_artifact_update(run_id: str, context_id: str, seq: int,
                         artifact_id: str, text: str, append: bool,
                         last_chunk: bool,
                         extra_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"seq": seq}
    if extra_metadata:
        meta.update(extra_metadata)
    return {"seq": seq, "type": "artifactUpdate", "ev": {
        "artifactUpdate": {
            "taskId": run_id, "contextId": context_id,
            "artifact": {"artifactId": artifact_id,
                          "parts": [{"text": text}]},
            "append": append, "lastChunk": last_chunk,
            "metadata": meta}}}


def wire_terminal_marker(run_id: str, context_id: str, state: str,
                         last_seq: int) -> dict[str, Any]:
    """Stage-4 terminal update; final + lastSeq in metadata."""
    return wire_status_update(run_id, context_id, state, seq=last_seq,
                              final=True, last_seq=last_seq)


def wire_inject_message(context_id: str, text: str,
                        message_id: str) -> dict[str, Any]:
    return {"seq": None, "type": "message", "ev": {
        "message": {"messageId": message_id, "contextId": context_id,
                     "role": "user",
                     "parts": [{"text": text}], "metadata": {}}}}


def wire_context_id_required() -> dict[str, Any]:
    return {"code": -32602, "message": "Invalid params: contextId required",
            "data": {"reason": "context_required"}}


def wire_unknown_outcome() -> dict[str, Any]:
    return {"code": -32000, "message": "unknown outcome",
            "data": {"reason": "unknown_outcome"}}


def wire_replay_refused(reason: str) -> dict[str, Any]:
    return {"code": -32000, "message": f"replay refused: {reason}",
            "data": {"reason": reason}}


def wire_interaction_requested(run_id: str, context_id: str, seq: int,
                               interaction_id: str,
                               payload: dict[str, Any]) -> dict[str, Any]:
    """HITL 请求（§8.6）：Task 转 INPUT_REQUIRED；元数据承载 interactionId
    与原始 payload 摘要（kind/prompt/text 降采样）。"""
    meta: dict[str, Any] = {"seq": seq, "interactionId": interaction_id}
    for key in ("kind", "phase", "toolCallId"):
        v = payload.get(key)
        if isinstance(v, (str, int, float, bool)):
            meta[key] = v
    prompt = payload.get("prompt") or payload.get("text")
    if isinstance(prompt, str) and prompt:
        meta["prompt"] = prompt[:200]
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
                          "metadata": meta}}}


def wire_interaction_resolved(run_id: str, context_id: str, seq: int,
                              payload: dict[str, Any]) -> dict[str, Any]:
    """HITL 决议（§8.6）：Task 回 WORKING；metadata.interactionId 保留对应关系。"""
    meta: dict[str, Any] = {"seq": seq}
    iid = payload.get("interactionId") or payload.get("id")
    if isinstance(iid, (str, int)):
        meta["interactionId"] = iid
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": "TASK_STATE_WORKING"},
                          "metadata": meta}}}


def wire_agent_progress(run_id: str, context_id: str, seq: int,
                        payload: dict[str, Any]) -> dict[str, Any]:
    """agent 事件（openclaw tool 进度等）→ statusUpdate（state 不变；metadata
    保 tool/phase/data 摘要——BaaS 客户端反向归一回 v3 `agent` 事件 payload）。"""
    meta: dict[str, Any] = {"seq": seq, "kind": "agent"}
    for key in ("tool", "phase", "type", "name"):
        v = payload.get(key)
        if isinstance(v, (str, int, float, bool)):
            meta[key] = v
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": "TASK_STATE_WORKING"},
                          "metadata": meta}}}
