"""Rule 25 conformance — EvaluationGatePlugin.

Consumer under test: ``PublishFlowService.process()`` consults
``EvaluationGatePlugin.check_publish_gate(...)`` before the
DRAFT→BUILDING CAS advance (v5.3 B2). The full flow is exercised in
``tests/community/core/service_bot/services/test_publish_flow_gate.py``;
this suite verifies the DI-bound impl's own contract:

- the bound gate publishes a not-hit policy (community column binds the
  allow-all impl, evaluation disabled by default — D10 safe default);
- the bound gate never blocks a draft publish (allow decision with a
  machine-readable status).
"""
from __future__ import annotations

from agentclaw.community.plugin_api.evaluation_gate import (
    GATE_NOT_HIT,
    GatePolicyInfo,
    GatePublishDecision,
    EvaluationGatePlugin,
)

import pytest


@pytest.mark.asyncio
async def test_bound_gate_policy_is_not_hit(world) -> None:
    plugin = world.get(EvaluationGatePlugin)
    info = await plugin.query_gate_policy(
        platform="TC", space_id="sp-1", bot_id="bot-1"
    )
    assert isinstance(info, GatePolicyInfo)
    assert isinstance(info.enabled, bool)
    # community 列：门禁未接线 —— 策略未命中（不暴露评测入口）。
    assert info.enabled is False
    assert info.hit is False
    assert info.hit_type == "none"


@pytest.mark.asyncio
async def test_bound_gate_never_blocks_publish(world) -> None:
    plugin = world.get(EvaluationGatePlugin)
    decision = await plugin.check_publish_gate(
        platform="TC",
        space_id="sp-1",
        bot_id="bot-1",
        owner_id="alice",
        operator="alice",
    )
    assert isinstance(decision, GatePublishDecision)
    # 下游消费者（PublishFlowService）契约：allowed=True → 不拦截。
    assert decision.allowed is True
    assert decision.status == GATE_NOT_HIT
    assert decision.reason  # 决策必须携带可读说明