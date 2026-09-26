"""
StubPerspectiveProvider

G1: Fusion Entry Layer

用于开发和测试的 Stub Perspective Provider。

这是一个 fake provider，返回固定的视角结果。
生产环境应替换为真实的 bot 调用实现。
"""

from __future__ import annotations

from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext
from src.domain.models.fusion_result import Perspective


# 预定义的 stub 响应
STUB_RESPONSES: dict[str, dict[str, str]] = {
    "dba": {
        "summary": "从数据库角度，该方案会增加索引维护成本，但整体可行。",
        "evidence": "涉及读多写少场景|索引命中率可接受",
    },
    "security": {
        "summary": "从安全角度，需要补充权限校验与审计日志，否则存在越权风险。",
        "evidence": "缺少细粒度权限边界|未明确审计策略",
    },
    "ops": {
        "summary": "从运维角度，需要补充监控告警和部署方案。",
        "evidence": "缺少监控指标|部署流程不明确",
    },
    "architect": {
        "summary": "从架构角度，方案整体设计合理，但需要关注扩展性。",
        "evidence": "模块划分清晰|接口设计合理",
    },
}


class StubPerspectiveProvider:
    """
    Stub Perspective Provider

    用于开发和测试的 fake provider。

    行为：
    - 对已知的 participant 返回预定义的视角
    - 对未知的 participant 返回默认视角
    - 不会抛出异常
    """

    def __init__(self, responses: dict[str, dict[str, str]] | None = None):
        """
        初始化

        Args:
            responses: 自定义响应映射，key 为 participant_id
        """
        self.responses = responses or STUB_RESPONSES

    def collect(self, context: PerspectiveContext) -> Perspective:
        """
        收集视角

        Args:
            context: 视角收集上下文

        Returns:
            Perspective: 收集到的视角
        """
        participant_id = context.participant_id

        if participant_id in self.responses:
            response = self.responses[participant_id]
            return Perspective(
                participant_id=participant_id,
                participant_type="bot",
                role="consultant",
                summary=response["summary"],
                confidence=0.85,
                evidence=response.get("evidence", "").split("|") if response.get("evidence") else [],
                status="completed",
            )

        # 对未知 participant 返回默认视角
        return Perspective(
            participant_id=participant_id,
            participant_type="bot",
            role="consultant",
            summary=f"从 {participant_id} 角度，{context.question}",
            confidence=0.75,
            evidence=[],
            status="completed",
        )


__all__ = [
    "StubPerspectiveProvider",
    "STUB_RESPONSES",
]