"""L5 Swarm 方法剖面服务组装(spec §4.3 / §5;Task 11)。

实现 ``SwarmMethodOpsServiceProtocol`` 十一个权威面操作,按 1000 行
CI 边界拆片组装(spec §5 "swarm+open 按 1000 行边界拆"):
- 共用骨架(构造注入 / Collet 身份解析 / 治理面前置)在
  ``swarm_method_service_base.py``;
- 计划面八操作(profile 声明 / 计划提交 + provenance 行 / 结构校验
  报告 / adopt / start / pause / resume / terminate)在
  ``swarm_plan_ops.py``;
- Stage 分配面三操作(allocate / complete / fail,含 §8.1 lease
  结构断言)在 ``swarm_stage_ops.py``。

RF#2(binding 非 ACTIVE 拒副作用操作)、结构 Swarm binding 守卫与
G1 ``check_swarm_op`` 授权在 ``_gate`` 单点承载;状态转换一律经 G4
声明式机器裁决(swarm_plan / swarm_stage / swarm_allocation 三键),
双平面边界(P1 无 Computation Plane 派发,execution record 只增不
改)与各操作编排裁决见各切片模块 docstring。
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.services.swarm_method_service_base import (  # noqa: F401
    SwarmMethodServiceBase,
)
from agentclaw.community.core.task.context_collab.services.swarm_plan_ops import (  # noqa: F401
    SwarmPlanOpsMixin,
)
from agentclaw.community.core.task.context_collab.services.swarm_stage_ops import (  # noqa: F401
    SwarmStageOpsMixin,
)


class SwarmMethodService(SwarmStageOpsMixin, SwarmPlanOpsMixin):
    """Swarm 十一操作用例类(组装声明;行为与裁决见切片 docstring)。

    构造注入(``@inject``)在 ``SwarmMethodServiceBase.__init__``,
    P2 替换 store / 授权器 / Collet 仓储任一实现零服务层改动;
    runtime ``isinstance`` 面由全部操作方法齐备承载
    (``SwarmMethodOpsServiceProtocol``)。
    """


__all__ = ["SwarmMethodService"]
