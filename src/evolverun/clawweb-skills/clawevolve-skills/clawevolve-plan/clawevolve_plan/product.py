from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .direct_goal.schema import (
    requested_bench_template_count,
    requested_task_success_rate,
)


@dataclass(frozen=True)
class PlanningBasis:
    """Explain, in product language, what evidence the plan is based on."""

    mode: str
    user_requirement: str
    source_schema_version: str
    source_path: str
    diagnosed_problem_modes: tuple[str, ...]
    evidence_case_count: int
    inspected_targets: tuple[str, ...]
    creation_scopes: tuple[str, ...]
    reference_files: tuple[str, ...]
    planned_deliverables: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "mode_label": {
                "direct_goal": "用户直接需求",
                "diagnose": "Diagnose 诊断结果",
                "insight_improvement": "Insight 治理证据",
                "plan_source": "冻结的 Plan Source",
            }.get(self.mode, self.mode),
            "user_requirement": self.user_requirement,
            "source_schema_version": self.source_schema_version,
            "source_path": self.source_path,
            "diagnosed_problem_modes": list(self.diagnosed_problem_modes),
            "evidence_case_count": self.evidence_case_count,
            "inspected_targets": list(self.inspected_targets),
            "creation_scopes": list(self.creation_scopes),
            "reference_files": list(self.reference_files),
            "planned_deliverables": [dict(item) for item in self.planned_deliverables],
        }


class PlanningBasisBuilder:
    def build(
        self,
        *,
        plan: dict[str, Any],
        spec: dict[str, Any],
        source_path: str = "",
    ) -> PlanningBasis:
        mode = str(spec.get("input_mode") or plan.get("input_mode") or "diagnose")
        intent = spec.get("user_intent") or {}
        clusters = spec.get("root_cause_clusters") or []
        return PlanningBasis(
            mode=mode,
            user_requirement=str(
                intent.get("raw_request")
                or intent.get("intent_text")
                or (spec.get("goal") or {}).get("goal_text")
                or ""
            ).strip(),
            source_schema_version=str(plan.get("schema_version") or ""),
            source_path=str(source_path or spec.get("input_plan_path") or ""),
            diagnosed_problem_modes=tuple(
                _unique(
                    item.get("evolution_failure_mode")
                    for item in clusters
                    if isinstance(item, dict)
                )
            ),
            evidence_case_count=len(plan.get("cases") or []),
            inspected_targets=tuple(
                _unique(
                    (spec.get("agentic_discovery") or {}).get(
                        "inspected_or_candidate_files"
                    )
                    or []
                )
            ),
            creation_scopes=tuple(_unique(spec.get("allowed_creation_scopes") or [])),
            reference_files=tuple(_unique(spec.get("reference_files") or [])),
            planned_deliverables=tuple(
                dict(item)
                for item in spec.get("planned_deliverables") or []
                if isinstance(item, dict)
            ),
        )


@dataclass(frozen=True)
class HardConstraint:
    key: str
    category: str
    expected: Any
    source: str
    mandatory: bool = True

    def as_dict(
        self, *, actual: Any = None, status: str = "preserved"
    ) -> dict[str, Any]:
        return {
            "key": self.key,
            "category": self.category,
            "expected": self.expected,
            "actual": actual,
            "source": self.source,
            "mandatory": self.mandatory,
            "status": status,
        }


@dataclass(frozen=True)
class GoalConstraintSet:
    raw_goal: str
    constraints: tuple[HardConstraint, ...]

    @classmethod
    def from_plan(
        cls, plan: dict[str, Any], *, goal_override: str = ""
    ) -> "GoalConstraintSet":
        context = (
            plan.get("goal_context")
            if isinstance(plan.get("goal_context"), dict)
            else {}
        )
        intent = (
            plan.get("user_intent") if isinstance(plan.get("user_intent"), dict) else {}
        )
        raw_goal = str(
            goal_override
            or context.get("raw_goal")
            or intent.get("raw_request")
            or intent.get("intent_text")
            or ""
        ).strip()
        constraints: list[HardConstraint] = []
        bench_count = requested_bench_template_count(raw_goal)
        if bench_count is not None:
            constraints.append(
                HardConstraint(
                    key="bench_template_count",
                    category="numeric",
                    expected=bench_count,
                    source="user_goal",
                )
            )
        success_rate = requested_task_success_rate(raw_goal)
        if success_rate is not None:
            constraints.append(
                HardConstraint(
                    key="primary_metric_target",
                    category="numeric",
                    expected=success_rate,
                    source="user_goal",
                )
            )
        semantic_sources = (
            ("required_capability", context.get("required_capabilities")),
            ("quality_requirement", context.get("quality_requirements")),
            ("business_constraint", context.get("constraints")),
            ("requested_deliverable", context.get("requested_deliverables")),
        )
        for category, values in semantic_sources:
            for index, value in enumerate(_strings(values), start=1):
                constraints.append(
                    HardConstraint(
                        key=f"{category}_{index}",
                        category=category,
                        expected=value,
                        source="goal_analysis",
                    )
                )
        return cls(raw_goal=raw_goal, constraints=tuple(constraints))


@dataclass(frozen=True)
class GoalFidelityReport:
    status: str
    constraints: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "constraints": [dict(item) for item in self.constraints],
            "warnings": list(self.warnings),
        }


class GoalFidelityValidator:
    """Fail on deterministic numeric loss; report semantic coverage conservatively."""

    def validate(
        self, spec: dict[str, Any], constraint_set: GoalConstraintSet
    ) -> GoalFidelityReport:
        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        failures: list[str] = []
        searchable = _searchable_product_text(spec)
        for constraint in constraint_set.constraints:
            actual = self._actual_value(spec, constraint)
            if constraint.category == "numeric":
                matched = _numbers_equal(actual, constraint.expected)
                status = "preserved" if matched else "violated"
                if not matched:
                    failures.append(
                        f"{constraint.key}: expected {constraint.expected}, got {actual}"
                    )
            else:
                matched = _semantic_coverage(str(constraint.expected), searchable)
                status = "preserved" if matched else "preserved_with_warning"
                if not matched:
                    warnings.append(
                        f"未能从规划摘要中确认语义要求“{constraint.expected}”的明确映射；"
                        "原始要求已保留，建议后续进化执行前人工复核。"
                    )
                actual = str(constraint.expected)
            rows.append(constraint.as_dict(actual=actual, status=status))
        if failures:
            raise ValueError(
                "Plan 丢失了用户数值硬约束，已阻止写出文档：" + "; ".join(failures)
            )
        return GoalFidelityReport(
            status="passed_with_warnings" if warnings else "passed",
            constraints=tuple(rows),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _actual_value(spec: dict[str, Any], constraint: HardConstraint) -> Any:
        if constraint.key == "bench_template_count":
            return int((spec.get("evidence") or {}).get("case_count") or 0)
        if constraint.key == "primary_metric_target":
            criteria = spec.get("acceptance_criteria") or {}
            primary_metric = criteria.get("primary_metric") or {}
            return primary_metric.get("target")
        return None


class PlanSummaryBuilder:
    def build(
        self,
        *,
        plan: dict[str, Any],
        spec: dict[str, Any],
        basis: PlanningBasis,
    ) -> dict[str, Any]:
        context = (
            plan.get("goal_context")
            if isinstance(plan.get("goal_context"), dict)
            else {}
        )
        expected_capabilities = _unique(
            list(_strings(context.get("required_capabilities")))
            + list(spec.get("required_behavior") or [])
        )
        directions = _unique(
            item.get("expected_effect") or item.get("why_this_round")
            for item in spec.get("active_optimization_directions") or []
            if isinstance(item, dict)
        )
        return {
            "goal": str((spec.get("goal") or {}).get("goal_text") or ""),
            "expected_capabilities": expected_capabilities,
            "optimization_directions": directions,
            "change_boundary": {
                "suggested_update_scope": list(
                    spec.get("allowed_update_targets") or []
                ),
                "creation_scopes": list(spec.get("allowed_creation_scopes") or []),
                "read_only_references": list(spec.get("reference_files") or []),
                "planned_deliverables": [
                    dict(item) for item in spec.get("planned_deliverables") or []
                ],
                "plan_is_read_only": True,
            },
            "acceptance_criteria": dict(spec.get("acceptance_criteria") or {}),
            "planning_basis": basis.as_dict(),
        }


class PlanProductService:
    """Attach all user-facing product views through one cohesive domain service."""

    def __init__(
        self,
        basis_builder: PlanningBasisBuilder | None = None,
        summary_builder: PlanSummaryBuilder | None = None,
        fidelity_validator: GoalFidelityValidator | None = None,
    ) -> None:
        self._basis_builder = basis_builder or PlanningBasisBuilder()
        self._summary_builder = summary_builder or PlanSummaryBuilder()
        self._fidelity_validator = fidelity_validator or GoalFidelityValidator()

    def enrich(
        self,
        *,
        plan: dict[str, Any],
        spec: dict[str, Any],
        goal_override: str = "",
        source_path: str = "",
    ) -> dict[str, Any]:
        basis = self._basis_builder.build(plan=plan, spec=spec, source_path=source_path)
        spec["planning_basis"] = basis.as_dict()
        spec["plan_summary"] = self._summary_builder.build(
            plan=plan, spec=spec, basis=basis
        )
        constraint_set = GoalConstraintSet.from_plan(plan, goal_override=goal_override)
        spec["goal_fidelity"] = self._fidelity_validator.validate(
            spec, constraint_set
        ).as_dict()
        return spec


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return _unique(value)


def _unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _numbers_equal(actual: Any, expected: Any) -> bool:
    try:
        return abs(float(actual) - float(expected)) <= 1e-9
    except (TypeError, ValueError):
        return False


def _searchable_product_text(spec: dict[str, Any]) -> str:
    values: list[str] = []
    summary = spec.get("plan_summary") or {}
    values.append(str(summary.get("goal") or ""))
    values.extend(str(item) for item in summary.get("expected_capabilities") or [])
    values.extend(str(item) for item in summary.get("optimization_directions") or [])
    values.extend(str(item) for item in spec.get("requested_deliverables") or [])
    values.extend(str(item) for item in spec.get("required_behavior") or [])
    return " ".join(values).lower()


def _semantic_coverage(expected: str, searchable: str) -> bool:
    normalized = "".join(expected.lower().split())
    haystack = "".join(searchable.lower().split())
    if normalized and normalized in haystack:
        return True
    tokens = [token for token in _semantic_tokens(expected) if len(token) >= 2]
    return (
        bool(tokens) and sum(token in haystack for token in tokens) / len(tokens) >= 0.6
    )


def _semantic_tokens(text: str) -> list[str]:
    cleaned = str(text or "").lower()
    for char in "，。；：、,.!?:;()（）[]【】`'\"":
        cleaned = cleaned.replace(char, " ")
    return [token for token in cleaned.split() if token]
