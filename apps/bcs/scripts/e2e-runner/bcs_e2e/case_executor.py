"""Deterministic execution spine for the reviewed case rewrites.

This module doesn't start a runtime or talk to BCS. It drives the reviewed
obligations in the exact order returned by each case rewrite plan, using
the protocol methods on a supplied adapter. It is the environment-neutral
execution spine; local, singlebox and preprod adapters remain responsible
for transport, identity, state and cleanup.
"""
from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall, validate_adapter_contract
from .case_rewrite import (
    CaseRewritePlan,
    fixed_loop_case_rewrite,
    slow_judge_case_rewrite,
)
from .run_state import RunStateError


@dataclass(frozen=True)
class StepOutcome:
    """The outcome of one original case obligation."""

    requirement_id: str
    method: str
    status: str
    data: object
    evidence: dict
    error_type: str | None
    error_gap: object = None
    error_detail: str | None = None

    def validate(self) -> None:
        if (self.status not in {'PASS', 'NOT_RUN'}
                or not isinstance(self.evidence, dict)
                or (self.status == 'PASS' and self.error_type is not None)):
            raise RunStateError(f'case execution step {self.requirement_id!r} is invalid')


@dataclass(frozen=True)
class CaseExecution:
    """The fixed execution progress for one adapter attempt."""

    case_id: str
    parent_id: str
    status: str
    steps: tuple[StepOutcome, ...]
    completed_count: int
    total_count: int
    error_type: str | None
    ready_to_execute: bool
    error_detail: str | None = None

    def validate(self) -> None:
        if (self.status not in {'PASS', 'BLOCKED'}
                or self.completed_count < 0
                or self.total_count <= 0
                or self.completed_count > self.total_count
                or len(self.steps) != self.total_count
                or (self.status == 'PASS'
                    and (self.completed_count != self.total_count or self.error_type is not None))):
            raise RunStateError('case execution summary is invalid')
        for step in self.steps:
            step.validate()

    def snapshot(self) -> dict:
        self.validate()
        return {
            'case_id': self.case_id,
            'parent_id': self.parent_id,
            'status': self.status,
            'completed_count': self.completed_count,
            'total_count': self.total_count,
            'ready_to_execute': self.ready_to_execute,
            'error_type': self.error_type,
            'error_detail': self.error_detail,
            'steps': [
                {
                    'requirement_id': step.requirement_id,
                    'method': step.method,
                    'status': step.status,
                    'data': step.data,
                    'evidence': step.evidence,
                    'error_type': step.error_type,
                    'error_gap': step.error_gap,
                    'error_detail': step.error_detail,
                }
                for step in self.steps
            ],
        }


def record_error_detail(error: BaseException) -> str | None:
    """Cap the first failing exception text for envelope diagnostics."""
    detail = str(error) or None
    if detail is not None and len(detail) > 800:
        detail = detail[:797] + "..."
    return detail


def execute_case(plan: CaseRewritePlan, adapter: object) -> CaseExecution:
    """Run every reviewed obligation of one case plan against an adapter."""
    validate_adapter_contract(adapter, plan)
    steps: list[StepOutcome] = []
    error_type: str | None = None
    first_error_detail: str | None = None
    completed = 0

    for requirement in plan.requirements:
        method_name = requirement.adapter_method
        method = getattr(adapter, method_name)
        if error_type is not None:
            step = StepOutcome(requirement_id=requirement.requirement_id,
                               method=method_name, status='NOT_RUN',
                               data=None, evidence={}, error_type=None)
        else:
            try:
                if not callable(method):
                    raise RunStateError(f'method {method_name} is not callable')
                result = method()
                if not isinstance(result, AdapterCall):
                    raise RunStateError(f'method {method_name} did not return AdapterCall')
                result.validate()
                step = StepOutcome(requirement_id=requirement.requirement_id,
                                   method=method_name, status='PASS',
                                   data=result.data, evidence=dict(result.evidence),
                                   error_type=None)
                completed += 1
            except Exception as error:
                error_type = type(error).__name__
                detail = record_error_detail(error)
                if first_error_detail is None:
                    first_error_detail = detail
                step = StepOutcome(requirement_id=requirement.requirement_id,
                                   method=method_name, status='NOT_RUN',
                                   data=None, evidence={},
                                   error_type=error_type,
                                   error_gap=getattr(error, "gap", None),
                                   error_detail=detail)
        steps.append(step)

    execution = CaseExecution(
        case_id=plan.case_id,
        parent_id=plan.parent_id,
        status='PASS' if error_type is None else 'BLOCKED',
        steps=tuple(steps),
        completed_count=completed,
        total_count=len(steps),
        error_type=error_type,
        ready_to_execute=error_type is None,
        error_detail=first_error_detail,
    )
    execution.validate()
    return execution


def execute_slow_judge_case(adapter: object) -> CaseExecution:
    """Run all 22 reviewed slow-Judge obligations against a supplied adapter."""
    return execute_case(slow_judge_case_rewrite(), adapter)


def execute_fixed_loop_case(adapter: object) -> CaseExecution:
    """Run all 8 reviewed fixed-Loop obligations against a supplied adapter."""
    return execute_case(fixed_loop_case_rewrite(), adapter)
