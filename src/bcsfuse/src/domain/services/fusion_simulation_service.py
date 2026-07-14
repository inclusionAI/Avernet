"""
Fusion Simulation Service

Worker Profile Retrieval & Fusion Simulation Baseline

融合模拟服务，支持 G1/G2/G5 模式的统一入口和内部分发。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from src.domain.models.fusion_request import FusionRequest
from src.domain.models.fusion_result import (
    FusionResult,
    FusionTiming,
    Perspective,
    Recommendation,
)
from src.domain.models.fusion_simulation_input import FusionSimulationInput
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.worker_context_digest import WorkerContextDigest
from src.domain.models.worker_profile import WorkerProfile


class FusionSimulationService:
    """
    融合模拟服务

    提供统一的融合模拟入口，根据模式分发到相应的处理器。

    模式说明：
    - G1 (AGENT): 专家咨询模式，关注直接相关性，生成单一建议
    - G2 (CONFLICT_ALIGNMENT): 冲突对齐模式，识别冲突和对齐点
    - G5 (EXPERT_DIAGNOSIS): 专家诊断模式，优先领域覆盖，生成全面诊断报告
    """

    def __init__(
        self,
        default_timeout_ms: int = 15000,
    ):
        """
        初始化服务

        Args:
            default_timeout_ms: 默认超时时间（毫秒）
        """
        self.default_timeout_ms = default_timeout_ms

    def simulate(self, input_data: FusionSimulationInput) -> FusionResult:
        """
        执行融合模拟

        Args:
            input_data: 模拟输入

        Returns:
            FusionResult: 融合结果
        """
        started_at = datetime.now()

        try:
            # 根据模式分发到相应的处理器
            if input_data.mode == RetrievalMode.AGENT:
                result = self._simulate_g1(input_data)
            elif input_data.mode == RetrievalMode.CONFLICT_ALIGNMENT:
                result = self._simulate_g2(input_data)
            elif input_data.mode == RetrievalMode.EXPERT_DIAGNOSIS:
                result = self._simulate_g5(input_data)
            else:
                # GENERAL 模式使用 G1 作为基础
                result = self._simulate_g1(input_data)

        except Exception as e:
            # 发生异常时返回错误结果
            finished_at = datetime.now()
            return FusionResult(
                group_id=self._generate_id("group"),
                fusion_id=self._generate_id("fusion"),
                question=input_data.question,
                perspectives=[],
                partial_success=False,
                warnings=[],
                errors=[str(e)],
                timing=FusionTiming(
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=int((finished_at - started_at).total_seconds() * 1000),
                ),
                fusion_mode=input_data.mode.value,
            )

        # 设置 timing 信息
        finished_at = datetime.now()
        result.timing = FusionTiming(
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
        )

        return result

    def simulate_from_request(
        self,
        fusion_request: FusionRequest,
        profiles: Optional[list[WorkerProfile]] = None,
        context_digests: Optional[list[WorkerContextDigest]] = None,
    ) -> FusionResult:
        """
        从 FusionRequest 执行模拟

        Args:
            fusion_request: 融合请求
            profiles: 可选的 profile 列表
            context_digests: 可选的上下文摘要列表

        Returns:
            FusionResult: 融合结果
        """
        input_data = FusionSimulationInput.from_fusion_request(
            fusion_request=fusion_request,
            profiles=profiles,
            context_digests=context_digests,
        )

        result = self.simulate(input_data)

        # 设置 driver_bot_id
        if fusion_request.driver_bot_id:
            result.driver_bot_id = fusion_request.driver_bot_id

        return result

    def _simulate_g1(self, input_data: FusionSimulationInput) -> FusionResult:
        """
        G1: 专家咨询模式

        关注直接相关性，生成单一建议。

        Args:
            input_data: 模拟输入

        Returns:
            FusionResult: 融合结果
        """
        warnings: list[str] = []
        perspectives: list[Perspective] = []

        # 检查 profiles
        if not input_data.profiles:
            return self._create_empty_result(
                input_data,
                warnings=["No profiles provided for simulation"],
                errors=[],
            )

        # 获取匹配的 context digests
        digest_map = {d.profile_key: d for d in input_data.context_digests}

        # 生成视角
        max_perspectives = input_data.max_perspectives
        for i, profile in enumerate(input_data.profiles[:max_perspectives]):
            digest = digest_map.get(profile.profile_key)

            # 根据相关性决定角色
            # 第一个 profile 通常是相关性最高的，作为 driver
            role = "driver" if i == 0 else "consultant"

            # 生成视角摘要
            summary = self._generate_perspective_summary(profile, digest, input_data.question)

            # 计算置信度
            confidence = self._calculate_confidence(profile, digest, input_data.question)

            perspective = Perspective(
                participant_id=profile.profile_key,
                participant_type="bot",  # 基于 WorkerProfile 通常是 bot
                role=role,
                summary=summary,
                confidence=confidence,
                evidence=self._extract_evidence(profile, digest),
                status="completed",
            )
            perspectives.append(perspective)

        # 生成建议
        recommendation = self._generate_recommendation_g1(
            perspectives, input_data.question
        )

        return FusionResult(
            group_id=self._generate_id("group"),
            fusion_id=self._generate_id("fusion"),
            question=input_data.question,
            perspectives=perspectives,
            recommendation=recommendation,
            partial_success=len(perspectives) > 0,
            warnings=warnings,
            errors=[],
            timing=FusionTiming(
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=0,
            ),
            fusion_mode="agent",
        )

    def _simulate_g2(self, input_data: FusionSimulationInput) -> FusionResult:
        """
        G2: 冲突对齐模式

        识别潜在冲突和对齐点。

        Args:
            input_data: 模拟输入

        Returns:
            FusionResult: 融合结果
        """
        warnings: list[str] = []
        perspectives: list[Perspective] = []
        key_insights: list[str] = []

        if not input_data.profiles:
            return self._create_empty_result(
                input_data,
                warnings=["No profiles provided for simulation"],
                errors=[],
            )

        digest_map = {d.profile_key: d for d in input_data.context_digests}

        # 生成多视角（G2 关注不同视角）
        max_perspectives = min(input_data.max_perspectives, len(input_data.profiles))

        for i, profile in enumerate(input_data.profiles[:max_perspectives]):
            digest = digest_map.get(profile.profile_key)

            # G2: 所有参与者都是 consultant，没有单一 driver
            perspective = Perspective(
                participant_id=profile.profile_key,
                participant_type="bot",
                role="consultant",
                summary=self._generate_perspective_summary(profile, digest, input_data.question),
                confidence=self._calculate_confidence(profile, digest, input_data.question),
                evidence=self._extract_evidence(profile, digest),
                status="completed",
                # G2 扩展字段
                key_points=self._extract_key_points(profile, digest),
                concerns=self._extract_concerns(profile, digest, input_data.question),
            )
            perspectives.append(perspective)

        # 识别关键洞察
        key_insights = self._identify_key_insights(perspectives, input_data.question)

        # 生成建议（G2 更关注对齐）
        recommendation = self._generate_recommendation_g2(
            perspectives, input_data.question, key_insights
        )

        return FusionResult(
            group_id=self._generate_id("group"),
            fusion_id=self._generate_id("fusion"),
            question=input_data.question,
            perspectives=perspectives,
            recommendation=recommendation,
            partial_success=len(perspectives) > 0,
            warnings=warnings,
            errors=[],
            timing=FusionTiming(
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=0,
            ),
            fusion_mode="conflict_alignment",
            key_insights=key_insights,
        )

    def _simulate_g5(self, input_data: FusionSimulationInput) -> FusionResult:
        """
        G5: 专家诊断模式

        优先领域覆盖，生成全面诊断报告。

        Args:
            input_data: 模拟输入

        Returns:
            FusionResult: 融合结果
        """
        warnings: list[str] = []
        perspectives: list[Perspective] = []

        if not input_data.profiles:
            return self._create_empty_result(
                input_data,
                warnings=["No profiles provided for simulation"],
                errors=[],
            )

        digest_map = {d.profile_key: d for d in input_data.context_digests}

        # G5: 确保领域多样性
        diverse_profiles = self._ensure_diversity(
            input_data.profiles, input_data.max_perspectives
        )

        for profile in diverse_profiles:
            digest = digest_map.get(profile.profile_key)

            # G5: 所有参与者都是专家
            perspective = Perspective(
                participant_id=profile.profile_key,
                participant_type="bot",
                role="expert",
                summary=self._generate_perspective_summary(profile, digest, input_data.question),
                confidence=self._calculate_confidence(profile, digest, input_data.question),
                evidence=self._extract_evidence(profile, digest),
                status="completed",
            )
            perspectives.append(perspective)

        # 生成诊断摘要
        summary = self._generate_diagnosis_summary(perspectives, input_data.question)

        # G5 建议通常是多个行动项
        recommendations = self._generate_expert_recommendations(perspectives, input_data.question)

        # 上线条件
        go_live_conditions = self._generate_go_live_conditions(perspectives)

        return FusionResult(
            group_id=self._generate_id("group"),
            fusion_id=self._generate_id("fusion"),
            question=input_data.question,
            perspectives=perspectives,
            recommendation=None,  # G5 使用 recommendations 列表
            partial_success=len(perspectives) > 0,
            warnings=warnings,
            errors=[],
            timing=FusionTiming(
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=0,
            ),
            fusion_mode="expert_diagnosis",
            summary=summary,
            recommendations=recommendations,
            go_live_conditions=go_live_conditions,
        )

    def _create_empty_result(
        self,
        input_data: FusionSimulationInput,
        warnings: list[str],
        errors: list[str],
    ) -> FusionResult:
        """创建空结果"""
        return FusionResult(
            group_id=self._generate_id("group"),
            fusion_id=self._generate_id("fusion"),
            question=input_data.question,
            perspectives=[],
            partial_success=False,
            warnings=warnings,
            errors=errors,
            timing=FusionTiming(
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=0,
            ),
            fusion_mode=input_data.mode.value,
        )

    def _generate_id(self, prefix: str) -> str:
        """生成唯一 ID"""
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def _generate_perspective_summary(
        self,
        profile: WorkerProfile,
        digest: Optional[WorkerContextDigest],
        question: str,
    ) -> str:
        """生成视角摘要"""
        # 基础信息
        skill_names = [s.name for s in profile.active_skills[:3]]
        skills_str = ", ".join(skill_names) if skill_names else "General expertise"

        # 如果有 digest，使用其摘要
        if digest and digest.context_summary:
            return f"[{skills_str}] {digest.context_summary}"

        # 基于上下文片段生成
        if profile.context_fragments:
            # 找到 AGENTS.md 或第一个片段
            agent_fragment = next(
                (f for f in profile.context_fragments if f.kind.value == "agent"),
                profile.context_fragments[0]
            )
            preview = agent_fragment.content[:200]
            return f"[{skills_str}] {preview}"

        return f"Expert with skills in: {skills_str}"

    def _calculate_confidence(
        self,
        profile: WorkerProfile,
        digest: Optional[WorkerContextDigest],
        question: str,
    ) -> float:
        """计算置信度"""
        base_confidence = 0.5

        # 如果有 digest，使用其评分
        if digest:
            # 使用选择比例作为相关性指标
            fragment_ratio = digest.fragment_selection_ratio
            skill_ratio = digest.skill_selection_ratio

            # 综合置信度
            confidence = base_confidence + (fragment_ratio * 0.25) + (skill_ratio * 0.25)
            return min(confidence, 1.0)

        # 基于技能匹配
        question_lower = question.lower()
        for skill in profile.active_skills:
            if skill.name.lower() in question_lower:
                base_confidence += 0.15

        return min(base_confidence, 1.0)

    def _extract_evidence(
        self,
        profile: WorkerProfile,
        digest: Optional[WorkerContextDigest],
    ) -> list[str]:
        """提取支持证据"""
        evidence: list[str] = []

        # 从 digest 的相关片段提取
        if digest and digest.relevant_fragments:
            for fragment in digest.relevant_fragments[:2]:
                preview = fragment.content[:100]
                evidence.append(f"From {fragment.filename}: {preview}")

        # 从技能提取
        for skill in profile.active_skills[:2]:
            evidence.append(f"Has skill: {skill.name}")

        return evidence

    def _extract_key_points(
        self,
        profile: WorkerProfile,
        digest: Optional[WorkerContextDigest],
    ) -> list[str]:
        """提取关键依据点（G2）"""
        key_points: list[str] = []

        # 从技能提取
        for skill in profile.active_skills[:3]:
            key_points.append(f"Expertise in {skill.name}")

        # 从上下文提取
        if digest and digest.relevant_fragments:
            for fragment in digest.relevant_fragments[:2]:
                key_points.append(f"Context from {fragment.kind.value}")

        return key_points

    def _extract_concerns(
        self,
        profile: WorkerProfile,
        digest: Optional[WorkerContextDigest],
        question: str,
    ) -> list[str]:
        """提取主要顾虑（G2）- 基于 profile 内容动态生成"""
        concerns: list[str] = []

        skill_names = [s.name.lower() for s in profile.active_skills]
        skill_descriptions = " ".join(
            s.description.lower() for s in profile.active_skills if s.description
        )
        question_lower = question.lower()

        # 从 context fragments 中提取已有覆盖的领域
        covered_topics: set[str] = set()
        if digest and digest.relevant_fragments:
            for fragment in digest.relevant_fragments:
                covered_topics.add(fragment.kind.value)
        for fragment in profile.context_fragments:
            covered_topics.add(fragment.kind.value)

        # 1. 基于问题关键词与技能覆盖的差异识别顾虑
        concern_keywords = {
            "security": ("安全审查", "security considerations"),
            "performance": ("性能评估", "performance implications"),
            "scalability": ("扩展性", "scalability concerns"),
            "reliability": ("可靠性", "reliability considerations"),
            "testing": ("测试覆盖", "testing coverage"),
            "documentation": ("文档完整性", "documentation completeness"),
            "monitoring": ("监控告警", "monitoring setup"),
            "data": ("数据处理", "data handling"),
            "api": ("接口设计", "API design"),
            "database": ("数据库", "database optimization"),
        }

        for keyword, (cn_desc, en_desc) in concern_keywords.items():
            if keyword in question_lower:
                # 检查是否有相关技能
                has_skill = any(keyword in name for name in skill_names)
                has_desc = keyword in skill_descriptions
                has_context = any(keyword in f.content.lower() for f in profile.context_fragments)

                if not (has_skill or has_desc or has_context):
                    concerns.append(f"Limited {en_desc} expertise in current profile")

        # 2. 基于上下文片段缺失识别顾虑
        if "agent" in question_lower or "ai" in question_lower:
            if "agent" not in covered_topics:
                concerns.append("Missing AGENTS.md context for AI-related question")

        # 3. 如果有 digest，基于选择比例识别顾虑
        if digest:
            if digest.fragment_selection_ratio < 0.3:
                concerns.append(
                    f"Only {digest.selected_fragments}/{digest.total_fragments} "
                    f"context fragments matched the question"
                )
            if digest.skill_selection_ratio < 0.3:
                concerns.append(
                    f"Limited skill relevance ({digest.selected_skills}/{digest.total_skills} matched)"
                )

        # 4. 如果没有发现问题，给出正面反馈
        if not concerns:
            concerns.append(f"Profile covers key areas: {', '.join(skill_names[:3])}")

        return concerns[:5]  # 最多 5 条顾虑

    def _identify_key_insights(
        self,
        perspectives: list[Perspective],
        question: str,
    ) -> list[str]:
        """识别关键洞察"""
        insights: list[str] = []

        # 收集所有技能
        all_skills: set[str] = set()
        for p in perspectives:
            for e in p.evidence:
                if e.startswith("Has skill:"):
                    all_skills.add(e.replace("Has skill: ", ""))

        if len(all_skills) > 1:
            insights.append(f"Multiple expertise areas involved: {', '.join(list(all_skills)[:3])}")

        # 分析顾虑
        all_concerns: list[str] = []
        for p in perspectives:
            all_concerns.extend(p.concerns)

        unique_concerns = set(all_concerns)
        if len(unique_concerns) > 1:
            insights.append("Diverse perspectives on potential risks")

        if not insights:
            insights.append("Consensus appears achievable among participants")

        return insights

    def _generate_recommendation_g1(
        self,
        perspectives: list[Perspective],
        question: str,
    ) -> Optional[Recommendation]:
        """生成 G1 建议"""
        if not perspectives:
            return None

        # 收集所有摘要
        summaries = [p.summary for p in perspectives]

        # 基于视角生成建议
        total_confidence = sum(p.confidence or 0 for p in perspectives) / len(perspectives)

        if total_confidence > 0.7:
            decision = "yes"
        elif total_confidence > 0.5:
            decision = "conditional_yes"
        else:
            decision = "needs_more_information"

        # 提取风险
        risks: list[str] = []
        for p in perspectives:
            for concern in p.concerns:
                if concern not in risks and "No major concerns" not in concern:
                    risks.append(concern)

        # 生成下一步行动
        next_actions: list[str] = []
        if perspectives:
            top_skills = []
            for e in perspectives[0].evidence:
                if e.startswith("Has skill:"):
                    top_skills.append(e.replace("Has skill: ", ""))
            if top_skills:
                next_actions.append(f"Leverage expertise in {', '.join(top_skills[:2])}")

        return Recommendation(
            summary=f"Based on {len(perspectives)} expert perspective(s), "
                    f"recommendation is: {decision}",
            decision=decision,
            risks=risks[:3],  # 最多 3 个风险
            next_actions=next_actions[:3],
        )

    def _generate_recommendation_g2(
        self,
        perspectives: list[Perspective],
        question: str,
        key_insights: list[str],
    ) -> Optional[Recommendation]:
        """生成 G2 建议"""
        if not perspectives:
            return None

        # G2 更关注对齐和冲突解决
        decision = "conditional_yes"  # G2 默认需要条件确认

        risks: list[str] = []
        for p in perspectives:
            for concern in p.concerns:
                if concern not in risks and "No major concerns" not in concern:
                    risks.append(concern)

        next_actions = [
            "Review alignment points from all perspectives",
            "Address identified concerns before proceeding",
        ]

        if key_insights:
            next_actions.append(f"Consider: {key_insights[0]}")

        return Recommendation(
            summary=f"Alignment recommendation based on {len(perspectives)} perspectives",
            decision=decision,
            risks=risks[:3],
            next_actions=next_actions[:3],
        )

    def _generate_diagnosis_summary(
        self,
        perspectives: list[Perspective],
        question: str,
    ) -> str:
        """生成诊断摘要（G5）"""
        if not perspectives:
            return "No expert perspectives available for diagnosis."

        # 收集所有专家领域
        all_skills: set[str] = set()
        for p in perspectives:
            for e in p.evidence:
                if e.startswith("Has skill:"):
                    all_skills.add(e.replace("Has skill: ", ""))

        skills_str = ", ".join(list(all_skills)[:5]) if all_skills else "various domains"

        return f"Diagnosis by {len(perspectives)} expert(s) covering: {skills_str}"

    def _generate_expert_recommendations(
        self,
        perspectives: list[Perspective],
        question: str,
    ) -> list[Any]:
        """生成专家建议列表（G5）- 基于真实 profile 数据"""
        recommendations: list[dict[str, Any]] = []

        for i, p in enumerate(perspectives):
            # 从 evidence 中提取具体行动建议
            actions: list[str] = []
            for evidence in p.evidence:
                if evidence.startswith("From "):
                    # 从上下文来源推导行动
                    actions.append(f"Leverage context from {evidence.split(':')[0].replace('From ', '')}")
                elif evidence.startswith("Has skill:"):
                    # 从技能推导行动
                    skill = evidence.replace("Has skill: ", "")
                    actions.append(f"Apply {skill} expertise to the solution")

            # 如果没有具体行动，基于 summary 生成
            if not actions:
                actions.append("Review and apply expert perspective")

            rec = {
                "priority": i + 1,
                "expert": p.participant_id,
                "action": actions[0] if actions else p.summary[:200],
                "supporting_actions": actions[1:3] if len(actions) > 1 else [],
                "confidence": p.confidence,
                "evidence_count": len(p.evidence),
                "key_points": p.key_points[:3] if p.key_points else [],
            }
            recommendations.append(rec)

        return recommendations

    def _generate_go_live_conditions(
        self,
        perspectives: list[Perspective],
    ) -> list[str]:
        """生成上线条件（G5）"""
        conditions: list[str] = [
            "All critical issues resolved",
            "Performance benchmarks met",
            "Security review completed",
        ]

        # 根据视角添加特定条件
        for p in perspectives:
            for evidence in p.evidence:
                if "security" in evidence.lower():
                    if "Security audit passed" not in conditions:
                        conditions.append("Security audit passed")
                    break

        return conditions[:5]  # 最多 5 个条件

    def _ensure_diversity(
        self,
        profiles: list[WorkerProfile],
        max_count: int,
    ) -> list[WorkerProfile]:
        """
        确保领域多样性

        贪心选择不同技能领域的 profiles。

        Args:
            profiles: 候选 profiles
            max_count: 最大数量

        Returns:
            多样化的 profiles 列表
        """
        if len(profiles) <= max_count:
            return profiles

        selected: list[WorkerProfile] = []
        selected_skills: set[str] = set()

        # 首先选择技能最多的 profiles（具有更广覆盖）
        sorted_profiles = sorted(
            profiles,
            key=lambda p: len(p.active_skills),
            reverse=True,
        )

        for profile in sorted_profiles:
            if len(selected) >= max_count:
                break

            profile_skills = set(s.name.lower() for s in profile.active_skills)
            new_skills = profile_skills - selected_skills

            # 如果带来了新技能领域，优先选择
            if new_skills or len(selected) < max_count:
                selected.append(profile)
                selected_skills.update(profile_skills)

        return selected


__all__ = ["FusionSimulationService"]