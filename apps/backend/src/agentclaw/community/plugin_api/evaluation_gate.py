"""EvaluationGatePlugin——草稿态→预发发布推进前的评测门禁决策 seam。

TeamClaw 评测门禁（PRD「工作台-评测」）：服务化 Bot 从草稿态推进预发发布
（DRAFT→BUILDING）前，由门禁平台判定本次推进是否放行。名单 / 策略 / 评测
执行 / 判定 / 审批全部由门禁平台侧承接；本协议只定义 community 发布流的
最小决策面，TC 侧不计算门禁是否通过。

Wiring（Rule 14，配置驱动）：
- ``PublishFlowService`` 以 Optional DI 参数消费本协议。
- prod：corp 绑定 HTTP 实现（门禁平台 agent-gate-list + 评测平台
  ``/eval/start``/``/eval/progress`` + 审批 OpenAPI）。
- community/local：绑定 allow-all 实现（不命中，原发布语义字节不变）。
- 未绑定（baas/secbaas、engine 等其它消费者）：默认 None → 完全跳过门禁，
  发布行为不变（跨消费者隔离，D7）。

D10 回滚语义：门禁总开关由 prod 实现内部经独立 DRM 开关
（EvaluationGate.config）控制，关闭时 prod 实现同样返回 not-hit/allowed 决策；
community 侧不感知开关。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol

from agentclaw.community.plugin_api.base import Plugin

# 门禁平台标识：TeamClaw 固定传 "TC"（契约见 TC交互接口汇总）。
PLATFORM_TEAMCLAW = "TC"

# 决策 status 取值（与 v5.3 方案 gate 语义对齐）：
GATE_ALLOWED = "allowed"  # 放行（未命中 / 评测通过 / 观察/正式放行）
GATE_NOT_HIT = "not_hit"  # 未命中名单（放行的特例，带上下文便于观测）
GATE_EVALUATING = "evaluating"  # 评测中（阻断，待评测完成）
GATE_EVAL_PASS = "eval_pass"  # 评测通过
GATE_EVAL_FAILED_OBSERVE = "eval_failed_observe"  # 不通过+观察模式（放行，带风险）
GATE_EVAL_FAILED_ENFORCE = "eval_failed_enforce"  # 不通过+正式模式（阻断）
GATE_ERROR = "gate_error"  # 门禁平台查询失败（阻断，PRD §14 不默认放行）


@dataclass(frozen=True)
class GatePolicyInfo:
    """名单命中查询结果（对应 check-start-gate 契约 + TC 侧开关状态）。

    enabled=False 表示 TC 侧门禁总开关关闭（策略视为未命中，前端不展示
    评测门禁入口）。
    """

    enabled: bool = False
    hit: bool = False
    hit_type: Optional[str] = None  # space | bot | none
    policy_id: Optional[str] = None
    policy_name: Optional[str] = None
    mode: Optional[str] = None  # observe | enforce
    reason: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GatePublishDecision:
    """发布推进决策（发布流唯一消费面）。

    allowed=False 时发布流不推进 DRAFT→BUILDING，reason 为面向用户的
    阻断文案；其余字段透传给发布单 ext.gate 与前端摘要展示。
    """

    allowed: bool
    status: str
    reason: str = ""
    hit: bool = False
    hit_type: Optional[str] = None
    mode: Optional[str] = None
    policy_name: Optional[str] = None
    failure_summary: Optional[str] = None
    report_url: Optional[str] = None
    # 观察模式风险提示等补充说明（PRD §11.5 文案）。
    data: Dict[str, Any] = field(default_factory=dict)


def not_hit_policy() -> GatePolicyInfo:
    """默认未命中策略（开关关闭 / 未绑定实现 / 查询异常兜底）。"""
    return GatePolicyInfo(enabled=False, hit=False, hit_type="none")


def allow_decision(status: str = GATE_ALLOWED, **overrides: Any) -> GatePublishDecision:
    """允许推进的决策。"""
    defaults: Dict[str, Any] = {"allowed": True, "status": status}
    defaults.update(overrides)
    return GatePublishDecision(**defaults)


class EvaluationGatePlugin(Plugin, Protocol):
    """评测门禁决策协议。

    两个方法均为 async：prod 实现内部走 HTTP（门禁/评测/审批平台）。
    """

    async def check_publish_gate(
        self,
        *,
        platform: str,
        space_id: str,
        bot_id: str,
        owner_id: str,
        operator: str,
    ) -> GatePublishDecision:
        """草稿态→预发发布推进前决策。

        返回 allowed=True 放行；allowed=False 阻断并在 reason 带用户文案。
        查询失败按 PRD §14 不默认放行（fail-closed），错误信息在 reason。
        """
        raise NotImplementedError

    async def query_gate_policy(
        self,
        *,
        platform: str,
        space_id: str,
        bot_id: str,
    ) -> GatePolicyInfo:
        """查询名单命中与门禁模式（前端策略弹窗/运维查询用）。"""
        raise NotImplementedError