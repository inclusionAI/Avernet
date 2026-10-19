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
                         last_seq: int,
                         detail: dict[str, Any] | None = None
                         ) -> dict[str, Any]:
    """Stage-4 terminal update; final + lastSeq in metadata.

    ``detail`` rides along on failure terminations (errorCode/errorMessage,
    engine_frame passthrough) so the A2A consumer resolves failures
    explainably instead of guessing from the bare state."""
    return wire_status_update(run_id, context_id, state, seq=last_seq,
                              final=True, last_seq=last_seq,
                              extra_metadata=detail)


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
    """HITL 请求（§8.6/R18-1）：INPUT_REQUIRED + **完整 engine_frame**
    （questions/kind/allowedDecisions 等结构化字段全量——BaaS 消费者按
    engine_frame 重建 payload，不摘要降采样）。"""
    meta: dict[str, Any] = {"seq": seq, "interactionId": interaction_id,
                            "engine_frame": dict(payload)}
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
                          "metadata": meta}}}


def wire_interaction_resolved(run_id: str, context_id: str, seq: int,
                              payload: dict[str, Any],
                              state: str | None = None) -> dict[str, Any]:
    """HITL 决议（§8.6/R18-1b/R5-2）：run 真实状态 + 完整 engine_frame。

    R18-1b：approval_resolved 只带 approvalId 的帧也要保住**交互身份**事件
    ——meta.interactionId 从 interactionId / id / approvalId 三键提取。

    R5-2：多 pending 部分决议时 run 仍是 INPUT_REQUIRED——帧状态必须反映
    真实状态，不得硬编码 WORKING（否则 SSE/Replay 说 run 已恢复而 GetTask
    说在等输入，对外语义自相矛盾）。消费端的"这是 resolved 事件"判定
    用 meta.kind == "interaction.resolved" 独立标识，**不依赖帧状态**：
    INPUT_REQUIRED 的 resolved 帧、新 requested 帧在 BaaS 分层派发下不再
    混淆。state 缺省 WORKING 仅为签名兼容，kernel 一律显式传真实状态。
    """
    meta: dict[str, Any] = {"seq": seq, "engine_frame": dict(payload),
                            "kind": "interaction.resolved"}
    iid = (payload.get("interactionId") or payload.get("id")
           or payload.get("approvalId"))
    if isinstance(iid, (str, int)):
        meta["interactionId"] = iid
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": state or "TASK_STATE_WORKING"},
                          "metadata": meta}}}


def wire_agent_progress(run_id: str, context_id: str, seq: int,
                        payload: dict[str, Any],
                        event_name: str = "agent") -> dict[str, Any]:
    """agent/mode_transition 事件 → statusUpdate(WORKING)。

    R17-2/16-6: 原始事件名经 ``metadata.original_event`` 保留——
    mode_transition.resolved 的 BaaS 消费者回调按它路由,不能被 "agent" 吞掉。
    完整原始载荷在 ``metadata.engine_frame`` (v2-E1' 无损通道)。
    """
    meta: dict[str, Any] = {"seq": seq, "kind": "agent",
                            "original_event": event_name,
                            "engine_frame": dict(payload)}
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": "TASK_STATE_WORKING"},
                          "metadata": meta}}}
