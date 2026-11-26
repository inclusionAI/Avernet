"""任务委托统一 Service API 再导出。

产品用例与 Bot 入站报告沿用既有合同;Swarm HTTP 和可信 worker 消费同一
TaskCommissionServiceProtocol 的 step_swarm/get_swarm_stages/interact_swarm。
Swarm 控制委托 L5 实现,不会把任务委托的产品标签限制套用到其他 Swarm。
"""

from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    TaskCommissionServiceProtocol,
)

__all__ = ["TaskCommissionServiceProtocol"]
