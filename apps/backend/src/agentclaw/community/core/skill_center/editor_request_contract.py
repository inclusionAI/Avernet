"""Result of a Team Space Skill editor application."""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.work_orders.models import WorkOrderStatus


@dataclass(frozen=True)
class SkillEditorRequestAdmission:
    """Qualified new request and the binding policy observed before creation."""

    auto_approve: bool
    skill_name: str


@dataclass(frozen=True)
class SkillEditorRequestResult:
    work_order_id: int
    work_order_no: str
    status: WorkOrderStatus
