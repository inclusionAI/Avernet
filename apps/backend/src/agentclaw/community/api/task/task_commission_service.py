"""任务代办产品协议再导出(模块级交付面,沿 bbs_public_discussion_service 先例)。

HTTP 对外交付后置(P1 裁决 2026-11-13);模块消费者经
``TaskCommissionServiceProtocol`` + task_module singleton 使用九用例
(发布 + Work 六动作 + 列表/详情);操作族映射见
products/task_commission/service_api.py docstring。
"""

from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    TaskCommissionServiceProtocol,
)

__all__ = ["TaskCommissionServiceProtocol"]
