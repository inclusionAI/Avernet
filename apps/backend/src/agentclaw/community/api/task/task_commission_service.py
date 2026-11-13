"""任务代办产品协议再导出(模块级交付面,沿 bbs_public_discussion_service 先例)。

Bot 入站上报由独立 A2A HTTP adapter 交付;用户 HTTP 交付仍后置。模块消费者经
``TaskCommissionServiceProtocol`` + task_module singleton 使用产品用例与受版本约束的执行者上报
(发布 + Work 六动作 + 列表/详情);操作族映射见
products/task_commission/service_api.py docstring。
"""

from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    TaskCommissionServiceProtocol,
)

__all__ = ["TaskCommissionServiceProtocol"]
