"""Conformance suite for ``TaskPlanServiceProtocol``.

Documented invariants:
  - ``generate_task_plans`` always returns a :class:`PlanReport` whose
    ``plans`` tuple has exactly four entries whose ``method`` values are
    ``A / B / C / D`` in that order, regardless of policy / LLM availability.
  - ``recommendation`` is one of the four method values.
  - the returned ``question`` / ``bot_id`` echo the call args (no mutation).
"""

from __future__ import annotations

from typing import Callable

from agentclaw.community.core.task.task_experience.protocols import (
    TaskPlanServiceProtocol,
)

__all__ = ["assert_task_plan_service_protocol_invariants", "PlanServiceFactory"]

PlanServiceFactory = Callable[[], TaskPlanServiceProtocol]


async def assert_task_plan_service_protocol_invariants(
    make_service: PlanServiceFactory,
) -> None:
    service = make_service()
    report = await service.generate_task_plans(question="sample q", bot_id="b1")

    assert report.question == "sample q"
    assert report.bot_id == "b1"
    # Exactly four plans in canonical method order.
    assert [p.method for p in report.plans] == ["A", "B", "C", "D"]
    assert len(report.plans) == 4
    # recommendation is one of the four method values, never arbitrary.
    assert report.recommendation in {"A", "B", "C", "D"}

    # Subsequent calls continue to satisfy the contract.
    report2 = await service.generate_task_plans(question="q2", bot_id="b2")
    assert [p.method for p in report2.plans] == ["A", "B", "C", "D"]
    assert report2.recommendation in {"A", "B", "C", "D"}
