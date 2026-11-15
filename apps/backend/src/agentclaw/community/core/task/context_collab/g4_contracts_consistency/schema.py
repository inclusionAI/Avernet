"""G4 合约一致性——事件注册表与 payload 校验(纯函数,无 IO)。

append-event 服务(后续任务)在写仓储前调用 ``validate_event_body`` 兜住
spec §11.4 的 G4 规则:未注册 event_kind 或不允许的 payload 组合必须拒绝。
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)

# P1 事件注册表(spec §11.4:未注册即拒)。
REGISTERED_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "task.created",
        "task.assigned",
        "task.started",
        "task.progress.updated",
        "task.submitted",
        "task.accepted",
        "task.rejected",
        "task.cancelled",
        "task.completed",
        "user_interaction.posted",
    }
)

ALLOWED_PRIMITIVE_TYPES: frozenset[str] = frozenset({"Message", "Task", "Activity"})
ALLOWED_VISIBILITY: frozenset[str] = frozenset({"public", "workspace", "private"})


def validate_event_body(
    *, event_kind: str, primitive_type: str, visibility: str, payload: dict,
) -> None:
    """校验事件体,非法即抛 ``ContextCollabValidationError``。规则:

    1. event_kind 必须在 ``REGISTERED_EVENT_KINDS``(spec §11.4);
    2. primitive_type / visibility 必须在各自白名单;
    3. event_kind=user_interaction.posted 时(spec §6.3、验收 §12.2 #3):
       - primitive_type 必须 Message;
       - payload.content 必须是非空 str;
       - payload 含 content_ref 即拒绝(消息内容必须内联)。
    """
    if event_kind not in REGISTERED_EVENT_KINDS:
        raise ContextCollabValidationError(
            f"unregistered event_kind: {event_kind!r}"
        )
    if primitive_type not in ALLOWED_PRIMITIVE_TYPES:
        raise ContextCollabValidationError(
            f"invalid primitive_type: {primitive_type!r}"
        )
    if visibility not in ALLOWED_VISIBILITY:
        raise ContextCollabValidationError(f"invalid visibility: {visibility!r}")

    if event_kind == "user_interaction.posted":
        if primitive_type != "Message":
            raise ContextCollabValidationError(
                "user_interaction.posted requires primitive_type 'Message'"
            )
        if not isinstance(payload, dict) or "content_ref" in payload:
            raise ContextCollabValidationError(
                "user_interaction.posted payload must not contain 'content_ref'"
            )
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ContextCollabValidationError(
                "user_interaction.posted payload requires non-empty 'content'"
            )
