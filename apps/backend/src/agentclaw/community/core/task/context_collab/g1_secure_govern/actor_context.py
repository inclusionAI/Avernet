"""已认证调用者上下文(G1 语义槽位)。

ACCS 一致性条款:"每个有副作用操作必须执行 G1 授权"——实现必须有一个
认证调用者载体。本类型即其权威居所(G1 层);``service_api.py`` 对其做兼容
再导出,既有 ``from …service_api import ActorContext`` 的 ~10 处 import
零破坏(2026-11-13 归位,spec bbs-public-discussion-product §3.2-I)。
由 delivery 从 auth 信息构造,核心不解析 HTTP。
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)


@dataclass(frozen=True)
class ActorContext:
    """已认证调用者上下文;G1 授权与审计的执行主体来源。"""

    actor: PrincipalRef
    source: str  # "api" | "openapi"
