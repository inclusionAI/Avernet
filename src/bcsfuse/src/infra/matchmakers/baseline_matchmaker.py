"""
BaselineMatchmaker Implementation

M6: Team Composer / Matchmaker

基于规则的团队组合匹配器实现。

Baseline 匹配策略：
1. 按能力匹配角色需求
2. 按负载均衡优选
3. 生成选择/排除解释
4. 识别缺口和警告
"""

from __future__ import annotations

from typing import Optional
import uuid

from src.domain.models.composition_input import CompositionInput, CompositionConstraints
from src.domain.models.composition_result import (
    CompositionResult,
    CompositionExplanation,
    CompositionWarning,
    CompositionError,
)
from src.domain.models.team_spec import TeamSpec, RoleAssignment
from src.domain.models.worker import Worker, Availability
from src.domain.models.candidate_bundle import CandidateBundle


class BaselineMatchmaker:
    """
    基线团队组合匹配器

    实现基于规则的团队组合策略。

    Strategies:
    - 能力匹配：根据 required_capabilities 和 role_requirements 匹配
    - 负载均衡：优先选择低负载 Worker
    - 可用性过滤：排除不可用 Worker
    """

    def compose(self, input_data: CompositionInput) -> CompositionResult:
        """
        执行团队组合

        Args:
            input_data: 组合输入

        Returns:
            CompositionResult: 组合结果
        """
        task_spec = input_data.task_spec
        plan_draft = input_data.plan_draft
        bundle = input_data.candidate_bundle
        constraints = input_data.constraints

        # 收集警告和错误
        warnings: list[CompositionWarning] = []
        errors: list[CompositionError] = []

        # Check if bundle has workers
        if not bundle.workers:
            errors.append(CompositionError(
                code="NO_CANDIDATES",
                message="No worker candidates available for team composition",
                details={"required_capabilities": task_spec.required_capabilities},
            ))
            return CompositionResult(
                team_spec=None,
                explanations=[],
                warnings=warnings,
                errors=errors,
            )

        # Filter available workers
        available_workers = [
            w for w in bundle.workers
            if w.state.availability == Availability.AVAILABLE
        ]

        if not available_workers:
            warnings.append(CompositionWarning(
                code="NO_AVAILABLE_WORKERS",
                message="No workers are currently available",
                details={"total_candidates": len(bundle.workers)},
            ))
            # Use all workers anyway as fallback
            available_workers = bundle.workers

        # Score and rank workers
        scored_workers = self._score_workers(
            available_workers,
            task_spec.required_capabilities,
            constraints,
        )

        # Select best workers within constraints
        selected_workers, selection_warnings = self._select_workers(
            scored_workers,
            constraints,
            plan_draft.role_requirements,
        )
        warnings.extend(selection_warnings)

        if not selected_workers:
            errors.append(CompositionError(
                code="NO_MATCHING_WORKERS",
                message="No workers match the required capabilities",
                details={"required_capabilities": task_spec.required_capabilities},
            ))
            return CompositionResult(
                team_spec=None,
                explanations=[],
                warnings=warnings,
                errors=errors,
            )

        # Generate explanations
        explanations = self._generate_explanations(
            scored_workers,
            selected_workers,
            task_spec.required_capabilities,
        )

        # Create role assignments
        role_assignments = self._create_role_assignments(
            selected_workers,
            plan_draft,
        )

        # Select skills and resources
        selected_skills = self._select_skills(bundle)
        selected_resources = self._select_resources(bundle, task_spec.required_resources)

        # Identify gaps
        gaps = self._identify_gaps(
            selected_workers,
            task_spec.required_capabilities,
            plan_draft.role_requirements,
        )

        # Generate composition rationale
        rationale = self._generate_rationale(
            selected_workers,
            task_spec.required_capabilities,
        )

        # Create TeamSpec
        team_id = f"team_{uuid.uuid4().hex[:8]}"
        team_spec = TeamSpec(
            team_id=team_id,
            members=[w.id for w in selected_workers],
            role_assignments=role_assignments,
            selected_skills=selected_skills,
            selected_resources=selected_resources,
            composition_rationale=rationale,
            gaps=gaps,
        )

        return CompositionResult(
            team_spec=team_spec,
            explanations=explanations,
            warnings=warnings,
            errors=errors,
        )

    def _score_workers(
        self,
        workers: list[Worker],
        required_capabilities: list[str],
        constraints: CompositionConstraints,
    ) -> list[tuple[Worker, float, dict[str, str]]]:
        """
        对 Worker 进行评分

        Returns:
            List of (worker, score, capability_match)
        """
        scored = []

        for worker in workers:
            score = 0.0
            capability_match: dict[str, str] = {}

            # Check capability matches
            for cap in worker.capabilities:
                if cap.name in required_capabilities:
                    # Score based on level
                    level_scores = {
                        "expert": 1.0,
                        "advanced": 0.8,
                        "intermediate": 0.6,
                        "novice": 0.4,
                    }
                    score += level_scores.get(cap.level, 0.5)
                    capability_match[cap.name] = cap.level

            # Normalize score by number of required capabilities
            if required_capabilities:
                score = score / len(required_capabilities)

            # Apply workload penalty if balancing is enabled
            if constraints.balance_workload and worker.state.current_load is not None:
                load_penalty = worker.state.current_load * 0.3
                score = score * (1.0 - load_penalty)

            scored.append((worker, score, capability_match))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored

    def _select_workers(
        self,
        scored_workers: list[tuple[Worker, float, dict[str, str]]],
        constraints: CompositionConstraints,
        role_requirements: list[str],
    ) -> tuple[list[Worker], list[CompositionWarning]]:
        """
        选择 Worker

        Returns:
            (selected_workers, warnings)
        """
        warnings: list[CompositionWarning] = []
        selected: list[Worker] = []

        max_size = constraints.max_team_size or len(scored_workers)

        # Select top workers that have some capability match
        for worker, score, cap_match in scored_workers:
            if len(selected) >= max_size:
                break

            # Only select workers with at least some match
            if score > 0 or len(cap_match) > 0:
                selected.append(worker)

        # Check min team size
        if len(selected) < constraints.min_team_size:
            warnings.append(CompositionWarning(
                code="INSUFFICIENT_TEAM_SIZE",
                message=f"Only {len(selected)} workers selected, needed {constraints.min_team_size}",
                details={"selected": len(selected), "required": constraints.min_team_size},
            ))

        # Check if all roles are covered
        if constraints.require_all_roles and role_requirements:
            assigned_roles = set()
            for worker in selected:
                # Simple heuristic: assign first matching role
                for role in role_requirements:
                    if role not in assigned_roles:
                        # Check if worker might fit this role based on domains
                        if role in worker.domains or any(role in cap.name for cap in worker.capabilities):
                            assigned_roles.add(role)
                            break

            missing_roles = set(role_requirements) - assigned_roles
            if missing_roles:
                warnings.append(CompositionWarning(
                    code="INCOMPLETE_ROLE_COVERAGE",
                    message=f"Not all roles are covered: {missing_roles}",
                    details={"missing_roles": list(missing_roles)},
                ))

        return selected, warnings

    def _generate_explanations(
        self,
        scored_workers: list[tuple[Worker, float, dict[str, str]]],
        selected_workers: list[Worker],
        required_capabilities: list[str],
    ) -> list[CompositionExplanation]:
        """生成解释"""
        explanations = []
        selected_ids = {w.id for w in selected_workers}

        for worker, score, cap_match in scored_workers:
            if worker.id in selected_ids:
                # Generate selection explanation
                reasons = []
                if cap_match:
                    reasons.append(f"Matches capabilities: {', '.join(cap_match.keys())}")
                if worker.state.current_load is not None and worker.state.current_load < 0.5:
                    reasons.append("Low current workload")

                explanation = CompositionExplanation(
                    worker_id=worker.id,
                    role=self._infer_role(worker, cap_match),
                    match_score=round(score, 2),
                    selection_reason="; ".join(reasons) if reasons else "Available candidate",
                    capability_match=cap_match,
                )
            else:
                # Generate exclusion explanation
                exclusion_reason = "Better candidates available"
                if score == 0:
                    exclusion_reason = "No matching capabilities"

                explanation = CompositionExplanation(
                    worker_id=worker.id,
                    role="",
                    match_score=round(score, 2),
                    selection_reason="",
                    capability_match=cap_match,
                    exclusion_reason=exclusion_reason,
                )

            explanations.append(explanation)

        return explanations

    def _create_role_assignments(
        self,
        selected_workers: list[Worker],
        plan_draft,
    ) -> list[RoleAssignment]:
        """创建角色分配"""
        assignments = []
        role_requirements = plan_draft.role_requirements
        steps = plan_draft.steps

        for i, worker in enumerate(selected_workers):
            # Assign role based on requirements or capabilities
            if role_requirements and i < len(role_requirements):
                role = role_requirements[i]
            elif worker.capabilities:
                role = worker.capabilities[0].name
            else:
                role = "contributor"

            # Get objective from steps or derive
            if steps and i < len(steps):
                objective = steps[i].objective
            else:
                objective = f"Contribute to {plan_draft.strategy}"

            assignments.append(RoleAssignment(
                worker_id=worker.id,
                role=role,
                objective=objective,
            ))

        return assignments

    def _infer_role(self, worker: Worker, cap_match: dict[str, str]) -> str:
        """推断角色"""
        if cap_match:
            # Use first matched capability as role hint
            return list(cap_match.keys())[0]
        if worker.capabilities:
            return worker.capabilities[0].name
        if worker.domains:
            return worker.domains[0]
        return "contributor"

    def _select_skills(self, bundle: CandidateBundle) -> list[str]:
        """选择技能"""
        # Select all skills from bundle for now
        # In a more sophisticated implementation, we would match skills to needs
        return [s.name for s in bundle.skills]

    def _select_resources(
        self,
        bundle: CandidateBundle,
        required_resources: list[str],
    ) -> list[str]:
        """选择资源"""
        # Select resources that match requirements
        available_ids = {r.id for r in bundle.resources}
        selected = []

        for req in required_resources:
            if req in available_ids:
                selected.append(req)

        # Also add any resources from bundle
        for resource in bundle.resources:
            if resource.id not in selected:
                selected.append(resource.id)

        return selected

    def _identify_gaps(
        self,
        selected_workers: list[Worker],
        required_capabilities: list[str],
        role_requirements: list[str],
    ) -> list[str]:
        """识别缺口"""
        gaps = []

        # Check for missing capabilities
        covered_capabilities: set[str] = set()
        for worker in selected_workers:
            for cap in worker.capabilities:
                covered_capabilities.add(cap.name)

        missing_caps = set(required_capabilities) - covered_capabilities
        for cap in missing_caps:
            gaps.append(f"Missing capability: {cap}")

        # Check for missing roles
        covered_roles: set[str] = set()
        for worker in selected_workers:
            covered_roles.update(worker.domains)

        missing_roles = set(role_requirements) - covered_roles
        for role in missing_roles:
            gaps.append(f"Missing role coverage: {role}")

        return gaps

    def _generate_rationale(
        self,
        selected_workers: list[Worker],
        required_capabilities: list[str],
    ) -> list[str]:
        """生成组合理由"""
        rationale = []

        for worker in selected_workers:
            matched_caps = [
                cap.name for cap in worker.capabilities
                if cap.name in required_capabilities
            ]
            if matched_caps:
                rationale.append(
                    f"{worker.identity.name} selected for: {', '.join(matched_caps)}"
                )
            else:
                rationale.append(
                    f"{worker.identity.name} selected as additional team member"
                )

        return rationale


__all__ = ["BaselineMatchmaker"]