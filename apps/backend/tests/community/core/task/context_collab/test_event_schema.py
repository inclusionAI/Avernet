"""G4 事件注册表与 payload 校验单测(Task 4)。

钉死 Review Focus #1:user_interaction.posted + Message 必须 payload.content
非空 str 且不允许 content_ref(spec §6.3、验收 §12.2 #3);未注册 event_kind
拒绝(spec §11.4);primitive_type / visibility 白名单。纯函数,无 IO。
"""

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.schema import (
    ALLOWED_PRIMITIVE_TYPES,
    ALLOWED_VISIBILITY,
    REGISTERED_EVENT_KINDS,
    validate_event_body,
)


def test_registry_constants_pinned():
    assert REGISTERED_EVENT_KINDS == frozenset(
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
    assert ALLOWED_PRIMITIVE_TYPES == frozenset({"Message", "Task", "Activity"})
    assert ALLOWED_VISIBILITY == frozenset({"public", "workspace", "private"})


def test_user_message_requires_inline_content():
    validate_event_body(
        event_kind="user_interaction.posted", primitive_type="Message",
        visibility="workspace", payload={"message_kind": "user_interaction", "content": "上下文做一下补充xxx"},
    )


@pytest.mark.parametrize("payload", [
    {"content_ref": "post_123"},                       # 出现 content_ref 即拒
    {"message_kind": "user_interaction"},               # 缺 content
    {"content": "   "},                                # 空 content
])
def test_user_message_rejects_content_ref_or_missing_content(payload):
    with pytest.raises(ContextCollabValidationError):
        validate_event_body(event_kind="user_interaction.posted", primitive_type="Message",
                            visibility="workspace", payload=payload)


def test_user_message_requires_message_primitive():
    with pytest.raises(ContextCollabValidationError):
        validate_event_body(event_kind="user_interaction.posted", primitive_type="Task",
                            visibility="workspace", payload={"content": "hello"})


def test_unregistered_event_kind_rejected():
    with pytest.raises(ContextCollabValidationError):
        validate_event_body(event_kind="artifact.delivered", primitive_type="Activity",
                            visibility="public", payload={})


@pytest.mark.parametrize("event_kind", sorted(
    REGISTERED_EVENT_KINDS - {"user_interaction.posted"}
))
def test_registered_task_kinds_pass(event_kind):
    validate_event_body(
        event_kind=event_kind, primitive_type="Activity",
        visibility="workspace", payload={},
    )


@pytest.mark.parametrize("primitive_type", ["message", "Artifact", ""])
def test_invalid_primitive_type_rejected(primitive_type):
    with pytest.raises(ContextCollabValidationError):
        validate_event_body(event_kind="task.progress.updated", primitive_type=primitive_type,
                            visibility="workspace", payload={})


@pytest.mark.parametrize("visibility", ["Public", "team", ""])
def test_invalid_visibility_rejected(visibility):
    with pytest.raises(ContextCollabValidationError):
        validate_event_body(event_kind="task.created", primitive_type="Task",
                            visibility=visibility, payload={})
