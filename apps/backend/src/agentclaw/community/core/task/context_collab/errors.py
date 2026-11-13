"""Context collaboration 领域错误类型(BBS context collab 各任务共享)。

继承 ``agentclaw.community.core.errors`` 的语义错误层级,不携带任何
HTTP/传输层信息;状态码映射由 adapter 层完成。
"""

from __future__ import annotations

from agentclaw.community.core.errors import Conflict, NotFound, ValidationError


class ContextCollabValidationError(ValidationError):
    """Context collab 输入校验失败(缺字段、空值、未知枚举 wire 值等)。"""


class ContextCollabNotFoundError(NotFound):
    """Context collab 目标聚合或事件不存在。"""


class ContextCollabVersionConflict(Conflict):
    """Context collab 写入与当前聚合版本/状态冲突。"""


class ContextCollabParentEventError(ValidationError):
    """事件 DAG 因果校验失败(parent_event 不存在或非法)。"""
