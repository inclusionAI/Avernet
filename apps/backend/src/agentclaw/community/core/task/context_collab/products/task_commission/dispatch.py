"""任务交办交付缝的 P1 默认实现(日志送达)。

发布成功后系统把任务信息"送达"指定执行 bot——这是 L1/A2A 渡口的 P1
占位形态(ACCS:"A2A Message 送达、Task 成功...都不等于上层 Operation
成功",交付是旁路,不回滚已持久化的协作权威)。P2 接真实通道
(engine adapter / A2A envelope)时经同一端口协议整体替换,发布编排
零改动。失败如实上抛,由调用方处置(发布已成功,不吞)。
"""

from __future__ import annotations

import logging

from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    DispatchedTaskInfo,
    TaskDispatchPort,
)

_logger = logging.getLogger(__name__)


class LoggingTaskDispatch(TaskDispatchPort):
    """P1 日志型送达:留痕不外发;DI singleton。"""

    def dispatch_task_info(self, info: DispatchedTaskInfo) -> None:
        _logger.info(
            "task commission dispatched: collet=%s assignee=%s title=%s",
            info.collet_id,
            info.assignee_agent_id,
            info.title,
        )
