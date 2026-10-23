"""Evaluation-gate concern——community binding（不命中，原发布语义）。"""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.evaluation_gate import EvaluationGatePlugin


class CommunityEvaluationGateModule(Module):
    """community: 门禁恒未命中，发布直接放行。"""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.community.evaluation_gate import (
            DirectEvaluationGate,
        )

        binder.bind(EvaluationGatePlugin, to=DirectEvaluationGate, scope=singleton)