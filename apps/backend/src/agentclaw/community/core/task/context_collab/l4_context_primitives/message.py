"""L4 Message 语义常量与语义投影(spec §6.3)。

用户新增互动消息的语义由 Event record 承载:wire 上 ``primitive_type``
为 ``Message``、``event_kind`` 以 ``message_kind.posted`` 形式出现,消息
正文必须直接放在 ``payload.content``。本模块只提供语义常量和薄值对象,
不提供独立消息表或仓库。
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)

PRIMITIVE_TYPE_MESSAGE = "Message"
"""wire 上的 ``primitive_type`` 取值(spec §6.3 参数表)。"""

MESSAGE_KIND_USER_INTERACTION = "user_interaction"
"""``payload.message_kind`` 的语义常量(spec §6.3 给出的取值)。"""

MESSAGE_EVENT_KIND_USER_INTERACTION_POSTED = "user_interaction.posted"
"""用户互动消息的 ``event_kind`` 语义常量(spec §6.3 示例)。"""

MESSAGE_CONTENT_FIELD = "content"
"""消息正文必须直接落在 ``payload.content``(spec §6.3 参数表)。"""


def _required_str(data: dict, key: str, owner: str) -> str:
    """从 dict 中取出必填字符串字段;缺失或空串抛校验错误。"""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string, got: {value!r}"
        )
    return value


@dataclass(frozen=True)
class Message:
    """用户互动消息的语义投影;事实由 Event record 的 payload 承载。"""

    message_kind: str
    content: str

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(
            message_kind=_required_str(data, "message_kind", "Message"),
            content=_required_str(data, "content", "Message"),
        )

    def to_dict(self) -> dict:
        return {"message_kind": self.message_kind, "content": self.content}
