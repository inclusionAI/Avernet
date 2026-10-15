"""DirectEvaluationGate——community 绑定：不命中原发布语义。

community 形态无门禁平台，策略恒为「未命中、开关关闭」，发布流保持既有
行为字节不变（跨消费者隔离，D7）。
"""
from __future__ import annotations

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.evaluation_gate import (
    GatePolicyInfo,
    GatePublishDecision,
    not_hit_policy,
    allow_decision,
)
from agentclaw.community.plugin_api.evaluation_gate import EvaluationGatePlugin

logger = get_logger()


class AllowAllEvaluationGate(EvaluationGatePlugin):
    """allow-all 门禁 seam 基类：名单恒未命中、发布恒放行。"""

    async def check_publish_gate(
        self,
        *,
        platform: str,
        space_id: str,
        bot_id: str,
        owner_id: str,
        operator: str,
    ) -> GatePublishDecision:
        logger.debug(
            "[AllowAllEvaluationGate] platform=%s bot_id=%s — gate not wired, allow",
            platform,
            bot_id,
        )
        return allow_decision(
            status="not_hit",
            hit=False,
            hit_type="none",
            reason="Evaluation gate not configured for this deployment, publish allowed",
        )

    async def query_gate_policy(
        self,
        *,
        platform: str,
        space_id: str,
        bot_id: str,
    ) -> GatePolicyInfo:
        return not_hit_policy()


class DirectEvaluationGate(AllowAllEvaluationGate):
    """community 实现：发布直接放行，与 DirectPublishApproval 语义对齐。"""