"""L5 Open 方法剖面服务组装(spec §4.4 / §5;Task 12)。

实现 ``OpenMethodOpsServiceProtocol`` 十个操作,与 Swarm 服务同款按
1000 行 CI 边界拆片组装(spec §5):
- 共用骨架(构造注入 / Collet 身份解析 / 治理面前置)在
  ``open_method_service_base.py``;
- 机会面五操作(publish / pause / reopen / close / submit)在
  ``open_opportunity_ops.py``;
- 申请与录取面五操作(screen / reject / withdraw / admit /
  revoke_admission + 录取产物编排)在 ``open_application_ops.py``。

RF#2(binding 非 ACTIVE 拒副作用操作)、结构 Open binding 守卫与
G1 ``check_open_op`` 授权在 ``_gate`` 单点承载;录取产物按
participation_type 的裁决与各操作编排见各切片模块 docstring。
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.services.open_application_ops import (  # noqa: F401
    OpenApplicationOpsMixin,
)
from agentclaw.community.core.task.context_collab.services.open_method_service_base import (  # noqa: F401
    OpenMethodServiceBase,
)
from agentclaw.community.core.task.context_collab.services.open_opportunity_ops import (  # noqa: F401
    OpenOpportunityOpsMixin,
)


class OpenMethodService(OpenApplicationOpsMixin, OpenOpportunityOpsMixin):
    """Open 十操作用例类(组装声明;行为与裁决见切片 docstring)。

    构造注入(``@inject``)在 ``OpenMethodServiceBase.__init__``,P2
    替换 store / 授权器 / Collet 仓储任一实现零服务层改动;runtime
    ``isinstance`` 面由全部操作方法齐备承载
    (``OpenMethodOpsServiceProtocol``)。
    """


__all__ = ["OpenMethodService"]
