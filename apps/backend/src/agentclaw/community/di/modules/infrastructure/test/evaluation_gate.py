"""Evaluation-gate concern——test / singlebox binding（本地 allow-all）。

test 树对 ``PublishFlowService`` 的门禁 seam 消费提供 allow-all 实现：
策略未命中、发布不拦截；需要 gate 行为的用例显式构造服务时注入
可编程 fake（见 tests/community/core/service_bot/services/
test_publish_flow_gate.py）。
"""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.evaluation_gate import EvaluationGatePlugin


class TestEvaluationGateModule(Module):
    """test / singlebox: 策略未命中、发布放行。"""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.local.evaluation_gate import (
            LocalEvaluationGate,
        )

        binder.bind(
            EvaluationGatePlugin, to=LocalEvaluationGate, scope=singleton
        )