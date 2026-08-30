"""任务回投契约(供外部 bot workflow / bcn 协作群 PUSH 回投)。对齐 plan §3.5.2 + 执行模块文档。

Re-export only. The Protocol is defined in its owning core module
(``core/task/task_loop_callback_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.task.task_loop_callback_protocol import (
    TaskCallbackData,
    TaskLoopCallbackProtocol,
)

__all__ = [
    "TaskCallbackData",
    "TaskLoopCallbackProtocol",
]
