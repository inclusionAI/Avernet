"""
Baseline Retriever Implementation

M5: Unified Retrieval Fabric

基于规则的 baseline retriever 实现。

职责：
- 基于能力、角色、知识需求等进行检索
- 支持基础过滤和排序
- 生成检索解释
- 返回结构化候选集

约束：
- 不接真实搜索引擎
- 不接向量库
- 通过构造函数注入数据
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from src.domain.services.retriever import Retriever
from src.domain.models.retrieval_input import RetrievalInput, RetrievalFilters
from src.domain.models.retrieval_result import RetrievalResult, RetrievalExplanation
from src.domain.models.candidate_bundle import CandidateBundle, KnowledgeItem
from src.domain.models.worker import Worker, SkillRef, ResourceRef, TrustLevel
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState


class CandidateCatalog(BaseModel):
    """
    候选目录

    存储所有可检索的候选对象。
    通过构造函数注入，不依赖 tests/fixtures。

    Fields:
        workers: Worker 列表
        knowledge_items: KnowledgeItem 列表
        skills: SkillRef 列表（独立技能目录）
        resources: ResourceRef 列表（独立资源目录）
    """
    workers: list[Worker] = []
    knowledge_items: list[KnowledgeItem] = []
    skills: list[SkillRef] = []
    resources: list[ResourceRef] = []

    model_config = {
        "extra": "forbid",
    }


class BaselineRetriever:
    """
    Baseline Retriever

    基于规则的 baseline 检索器实现。

    检索策略：
    1. Worker 检索：
       - 基于 TaskSpec.required_capabilities 匹配
       - 基于 PlanDraft.role_requirements 匹配 responsibilities
       - 应用 filters (worker_types, domains, trust_levels)
       - Stage 1 Phase 4: 默认过滤非 active / 非 online worker
       - 按 score 排序

    2. Knowledge 检索：
       - 基于 PlanDraft.knowledge_requirements 匹配 tags

    3. Skill 检索：
       - 从匹配的 Worker 聚合其 skills

    4. Resource 检索：
       - 基于 PlanDraft.resource_requirements 匹配 id
       - 从匹配的 Worker 聚合其 resources

    5. 解释生成：
       - 为每个匹配的候选生成 explanation

    Stage 1 Phase 4 规则：
    - 默认只返回 lifecycle_state == active 且 runtime_state == online 的 worker
    - 可通过 filter_inactive_workers=False 禁用
    """

    def __init__(
        self,
        catalog: CandidateCatalog,
        filter_inactive_workers: bool = False,
    ):
        """
        初始化 BaselineRetriever

        Args:
            catalog: 候选目录，包含所有可检索对象
            filter_inactive_workers: 是否过滤非 active/non-online worker
                - False（默认）: 不过滤（后向兼容）
                - True: 只返回 active + online 的 worker
        """
        self._catalog = catalog
        self._filter_inactive_workers = filter_inactive_workers

    def retrieve(self, input_data: RetrievalInput) -> RetrievalResult:
        """
        执行检索

        Args:
            input_data: 检索输入

        Returns:
            RetrievalResult: 检索结果
        """
        warnings: list[str] = []
        errors: list[str] = []
        explanations: list[RetrievalExplanation] = []

        # 1. Retrieve Workers
        workers, worker_explanations = self._retrieve_workers(input_data)
        explanations.extend(worker_explanations)

        # 2. Retrieve Knowledge Items
        knowledge_items, knowledge_explanations = self._retrieve_knowledge(input_data)
        explanations.extend(knowledge_explanations)

        # 3. Aggregate Skills from matched Workers
        skills, skill_explanations = self._aggregate_skills(workers)
        explanations.extend(skill_explanations)

        # 4. Retrieve Resources
        resources, resource_explanations = self._retrieve_resources(input_data, workers)
        explanations.extend(resource_explanations)

        # 5. Collect evidence
        evidence = self._collect_evidence(workers, knowledge_items)

        # 6. Generate warnings for empty results
        if len(workers) == 0:
            warnings.append("No workers matched the required capabilities or role requirements")
        if len(knowledge_items) == 0 and len(input_data.plan_draft.knowledge_requirements) > 0:
            warnings.append("No knowledge items matched the knowledge requirements")
        if len(resources) == 0 and len(input_data.plan_draft.resource_requirements) > 0:
            warnings.append("No resources matched the resource requirements")

        # 7. Build result
        bundle = CandidateBundle(
            workers=workers,
            knowledge_items=knowledge_items,
            skills=skills,
            resources=resources,
            evidence=evidence,
        )

        return RetrievalResult(
            candidate_bundle=bundle,
            warnings=warnings,
            errors=errors,
            explanations=explanations,
        )

    def _retrieve_workers(
        self, input_data: RetrievalInput
    ) -> tuple[list[Worker], list[RetrievalExplanation]]:
        """
        检索 Worker

        基于：
        - TaskSpec.required_capabilities
        - PlanDraft.role_requirements

        支持 filters: worker_types, domains, trust_levels, top_k

        Stage 1 Phase 4:
        - 默认过滤非 active / 非 online worker
        """
        task_spec = input_data.task_spec
        plan_draft = input_data.plan_draft
        filters = input_data.filters

        required_capabilities = set(task_spec.required_capabilities)
        role_requirements = set(plan_draft.role_requirements)

        matched_workers: list[tuple[Worker, float, list[str], str]] = []

        for worker in self._catalog.workers:
            # Stage 1 Phase 4: Filter inactive/offline workers
            if self._filter_inactive_workers:
                if not self._is_worker_active_and_online(worker):
                    continue

            # Apply filters first
            if filters:
                if not self._passes_filters(worker, filters):
                    continue

            # Calculate match score
            matched_fields: list[str] = []
            match_reasons: list[str] = []
            score = 0.0

            # Match capabilities
            worker_capabilities = {cap.name for cap in worker.capabilities}
            cap_matches = required_capabilities & worker_capabilities
            if cap_matches:
                matched_fields.extend([f"capabilities.{cap}" for cap in cap_matches])
                match_reasons.append(f"Worker has capabilities: {', '.join(cap_matches)}")
                score += len(cap_matches) / len(required_capabilities) * 0.5

            # Match responsibilities (roles)
            worker_responsibilities = set(worker.responsibilities)
            role_matches = self._match_roles(role_requirements, worker_responsibilities)
            if role_matches:
                matched_fields.extend([f"responsibilities.{r}" for r in role_matches])
                match_reasons.append(f"Worker has roles: {', '.join(role_matches)}")
                score += len(role_matches) / len(role_requirements) * 0.3 if role_requirements else 0

            # Bonus for domain match
            if filters and filters.domains:
                domain_matches = set(filters.domains) & set(worker.domains)
                if domain_matches:
                    score += 0.1

            # Bonus for trust level match
            if filters and filters.trust_levels:
                effective = worker.state.trust_level
                if effective == TrustLevel.UNVERIFIED:
                    effective = TrustLevel.SANDBOX_ONLY
                if effective in filters.trust_levels:
                    score += 0.1

            # Only include if there's at least one match
            if matched_fields:
                match_reason = ". ".join(match_reasons) if match_reasons else "Partial match"
                matched_workers.append((worker, min(score, 1.0), matched_fields, match_reason))

        # Sort by score descending
        matched_workers.sort(key=lambda x: x[1], reverse=True)

        # Apply top_k
        top_k = None
        if filters and filters.top_k:
            top_k = filters.top_k
        if top_k:
            matched_workers = matched_workers[:top_k]

        # Build explanations
        explanations: list[RetrievalExplanation] = []
        for worker, score, matched_fields, match_reason in matched_workers:
            explanations.append(RetrievalExplanation(
                candidate_type="worker",
                candidate_id=worker.id,
                matched_fields=matched_fields,
                match_reason=match_reason,
                score=score,
            ))

        return [w[0] for w in matched_workers], explanations

    def _passes_filters(self, worker: Worker, filters: RetrievalFilters) -> bool:
        """检查 Worker 是否通过过滤条件"""
        # Filter by worker_types
        if filters.worker_types:
            if worker.type not in filters.worker_types:
                return False

        # Filter by domains
        if filters.domains:
            if not any(d in worker.domains for d in filters.domains):
                return False

        # Filter by trust_levels
        if filters.trust_levels:
            # UNVERIFIED workers are treated as SANDBOX_ONLY for retrieval
            effective_trust = worker.state.trust_level
            if effective_trust == TrustLevel.UNVERIFIED:
                effective_trust = TrustLevel.SANDBOX_ONLY
            if effective_trust not in filters.trust_levels:
                return False

        return True

    def _is_worker_active_and_online(self, worker: Worker) -> bool:
        """
        检查 Worker 是否 active 且 online

        Stage 1 Phase 4 规则：
        - lifecycle_state 必须为 active
        - runtime_state 必须为 online

        Args:
            worker: Worker 对象

        Returns:
            是否 active 且 online
        """
        # 检查 lifecycle_state
        lifecycle = worker.lifecycle_state
        if hasattr(lifecycle, 'value'):
            lifecycle = lifecycle.value
        if lifecycle != WorkerLifecycleState.ACTIVE.value:
            return False

        # 检查 runtime_state
        runtime = worker.state.runtime_state
        if hasattr(runtime, 'value'):
            runtime = runtime.value
        if runtime != WorkerRuntimeState.ONLINE.value:
            return False

        return True

    def _match_roles(
        self, role_requirements: set[str], responsibilities: set[str]
    ) -> set[str]:
        """
        匹配角色需求与职责

        使用简单的关键词映射。
        """
        # Simple role to responsibility mapping
        role_mapping = {
            "researcher": {"research", "information_gathering", "analysis"},
            "analyst": {"analysis", "data_analysis", "report_generation"},
            "architect": {"architecture_design", "system_design", "technical_planning"},
            "developer": {"coding", "development", "testing", "debugging"},
            "reviewer": {"code_review", "review", "approval"},
            "coordinator": {"coordination", "planning", "management"},
        }

        matched_roles: set[str] = set()
        for role in role_requirements:
            role_lower = role.lower()
            # Direct match
            if role_lower in responsibilities:
                matched_roles.add(role)
                continue
            # Mapped match
            if role_lower in role_mapping:
                mapped_resps = role_mapping[role_lower]
                if mapped_resps & responsibilities:
                    matched_roles.add(role)

        return matched_roles

    def _retrieve_knowledge(
        self, input_data: RetrievalInput
    ) -> tuple[list[KnowledgeItem], list[RetrievalExplanation]]:
        """
        检索 Knowledge Items

        基于 PlanDraft.knowledge_requirements 匹配 tags
        """
        plan_draft = input_data.plan_draft
        filters = input_data.filters

        knowledge_requirements = set(plan_draft.knowledge_requirements)

        if not knowledge_requirements:
            return [], []

        matched_items: list[tuple[KnowledgeItem, float, list[str], str]] = []

        for ki in self._catalog.knowledge_items:
            matched_fields: list[str] = []
            score = 0.0

            # Match tags with knowledge requirements
            ki_tags = set(ki.tags) if ki.tags else set()
            tag_matches = knowledge_requirements & ki_tags
            if tag_matches:
                matched_fields.extend([f"tags.{tag}" for tag in tag_matches])
                score = len(tag_matches) / len(knowledge_requirements)

            # Also match title/summary keywords
            ki_text = f"{ki.title} {ki.summary}".lower()
            for req in knowledge_requirements:
                if req.lower() in ki_text:
                    if f"content.{req}" not in matched_fields:
                        matched_fields.append(f"content.{req}")
                        score += 0.1

            if matched_fields:
                score = min(score, 1.0)
                match_reason = f"Knowledge item matches requirements: {', '.join(knowledge_requirements & ki_tags) if (knowledge_requirements & ki_tags) else 'content match'}"
                matched_items.append((ki, score, matched_fields, match_reason))

        # Sort by score descending
        matched_items.sort(key=lambda x: x[1], reverse=True)

        # Apply top_k
        top_k = None
        if filters and filters.top_k:
            top_k = filters.top_k
        if top_k:
            matched_items = matched_items[:top_k]

        # Build explanations
        explanations: list[RetrievalExplanation] = []
        for ki, score, matched_fields, match_reason in matched_items:
            explanations.append(RetrievalExplanation(
                candidate_type="knowledge",
                candidate_id=ki.id,
                matched_fields=matched_fields,
                match_reason=match_reason,
                score=score,
            ))

        return [item[0] for item in matched_items], explanations

    def _aggregate_skills(
        self, workers: list[Worker]
    ) -> tuple[list[SkillRef], list[RetrievalExplanation]]:
        """
        聚合匹配 Worker 的 Skills

        去重并保持顺序，同时生成 explanations
        """
        seen_names: set[str] = set()
        skills: list[SkillRef] = []
        explanations: list[RetrievalExplanation] = []

        for worker in workers:
            for skill in worker.skills:
                if skill.name not in seen_names:
                    seen_names.add(skill.name)
                    skills.append(skill)
                    explanations.append(RetrievalExplanation(
                        candidate_type="skill",
                        candidate_id=skill.name,
                        matched_fields=[f"name.{skill.name}"],
                        match_reason=f"Skill from matched worker: {worker.id}",
                        score=0.8,
                    ))

        return skills, explanations

    def _retrieve_resources(
        self,
        input_data: RetrievalInput,
        matched_workers: list[Worker],
    ) -> tuple[list[ResourceRef], list[RetrievalExplanation]]:
        """
        检索 Resources

        基于：
        - PlanDraft.resource_requirements 匹配 id
        - 从匹配的 Worker 聚合其 resources
        """
        plan_draft = input_data.plan_draft
        filters = input_data.filters

        resource_requirements = set(plan_draft.resource_requirements)

        # Collect from requirements
        required_resources: list[tuple[ResourceRef, float, list[str], str]] = []
        for resource in self._catalog.resources:
            if resource.id in resource_requirements:
                required_resources.append((
                    resource,
                    1.0,
                    [f"id.{resource.id}"],
                    f"Resource ID matches requirement: {resource.id}",
                ))

        # Collect from matched workers
        worker_resources: list[tuple[ResourceRef, float, list[str], str]] = []
        seen_ids: set[str] = {r[0].id for r in required_resources}

        for worker in matched_workers:
            for resource in worker.resources:
                if resource.id not in seen_ids:
                    seen_ids.add(resource.id)
                    worker_resources.append((
                        resource,
                        0.7,
                        [f"worker_resource.{resource.id}"],
                        f"Resource from matched worker: {worker.id}",
                    ))

        # Combine and sort
        all_resources = required_resources + worker_resources
        all_resources.sort(key=lambda x: x[1], reverse=True)

        # Apply top_k
        top_k = None
        if filters and filters.top_k:
            top_k = filters.top_k
        if top_k:
            all_resources = all_resources[:top_k]

        # Build explanations
        explanations: list[RetrievalExplanation] = []
        for resource, score, matched_fields, match_reason in all_resources:
            explanations.append(RetrievalExplanation(
                candidate_type="resource",
                candidate_id=resource.id,
                matched_fields=matched_fields,
                match_reason=match_reason,
                score=score,
            ))

        return [r[0] for r in all_resources], explanations

    def _collect_evidence(
        self,
        workers: list[Worker],
        knowledge_items: list[KnowledgeItem],
    ) -> list[str]:
        """
        收集证据引用

        从 Worker 和 KnowledgeItem 中收集相关的证据 URI
        """
        evidence: list[str] = []

        # From workers
        for worker in workers:
            # Add worker ID as evidence reference
            evidence.append(f"worker:{worker.id}")
            # Add capability evidence refs
            for cap in worker.capabilities:
                for ref in cap.evidence_refs:
                    if ref not in evidence:
                        evidence.append(ref)

        # From knowledge items
        for ki in knowledge_items:
            if ki.source_uri:
                evidence.append(f"knowledge:{ki.id}:{ki.source_uri}")

        return evidence


__all__ = [
    "BaselineRetriever",
    "CandidateCatalog",
]