"""
ConflictAlignmentService

G2: Conflict Alignment Layer

冲突对齐服务，负责 G2 场景的冲突检测、对齐点提取和关键洞察生成。

职责：
1. 冲突提取（三层Fallback架构）
2. 对齐点提取
3. 关键洞察生成
4. 调用 FusionRecommendationService 生成建议（可选）

三层Fallback架构：
- Layer 1: LLM深度研判（最智能，语义理解）
- Layer 2: V2结构化分析增强（LLM立场提取 + 规则判定）
- Layer 3: Legacy规则匹配（关键词 + 模板，最稳定）

约束：
- 支持 partial success
- 每层都有Feature Flag控制
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from src.domain.models.fusion_result import (
    FusionResult,
    Perspective,
    Recommendation,
    FusionTiming,
)
from src.domain.models.fusion_conflict import FusionConflict
from src.domain.models.fusion_alignment import FusionAlignmentPoint
from src.domain.models.fusion_conflict_conclusion import ConflictConclusion
from src.infra.config.feature_flags import FeatureFlags
from src.infra.observability.g2_metrics import get_g2_metrics

if TYPE_CHECKING:
    from src.application.services.fusion_recommendation_service import FusionRecommendationService
    from src.application.services.structured_signal_extractor import StructuredSignalExtractor
    from src.application.services.llm_conflict_analyzer import LLMConflictAnalyzer
    from src.application.services.llm_stance_extractor import LLMStanceExtractor
    from src.domain.services.conflict_dimension_analyzer import ConflictDimensionAnalyzer

logger = logging.getLogger(__name__)


# =====================================
# 分析结果数据类
# =====================================

class AnalysisResult:
    """
    冲突分析结果（内部使用）

    统一三层的分析结果格式。
    """
    def __init__(
        self,
        conflicts: list[FusionConflict],
        alignment_points: list[FusionAlignmentPoint],
        conclusion: Optional[ConflictConclusion],
        source: str,  # "llm" | "v2" | "legacy"
        llm_analysis: Any = None,
        structured_analysis: Any = None,
    ):
        self.conflicts = conflicts
        self.alignment_points = alignment_points
        self.conclusion = conclusion
        self.source = source
        self.llm_analysis = llm_analysis
        self.structured_analysis = structured_analysis


class ConflictAlignmentService:
    """
    冲突对齐服务

    负责 G2: Conflict Alignment 场景的核心处理逻辑。

    三层Fallback架构：
    - Layer 1: LLM深度研判（LLMConflictAnalyzer）
    - Layer 2: V2结构化分析增强（LLMStanceExtractor + ConflictDimensionAnalyzer）
    - Layer 3: Legacy规则匹配（现有规则实现）

    核心方法：
    - align(): 执行冲突对齐

    Attributes:
        _recommendation_service: 融合建议服务（可选）
        _llm_analyzer: Layer 1 LLM冲突分析器（可选）
        _llm_stance_extractor: Layer 2 LLM立场提取器（可选）
        _signal_extractor: Layer 2 关键词信号提取器（V2，可选）
        _conflict_analyzer: Layer 2 冲突维度分析器（V2，可选）
    """

    def __init__(
        self,
        recommendation_service: Optional["FusionRecommendationService"] = None,
        llm_analyzer: Optional["LLMConflictAnalyzer"] = None,
        llm_stance_extractor: Optional["LLMStanceExtractor"] = None,
        signal_extractor: Optional["StructuredSignalExtractor"] = None,
        conflict_analyzer: Optional["ConflictDimensionAnalyzer"] = None,
    ):
        """
        初始化服务

        Args:
            recommendation_service: 融合建议服务（可选，用于生成 recommendation）
            llm_analyzer: Layer 1 LLM冲突分析器（可选）
            llm_stance_extractor: Layer 2 LLM立场提取器（可选）
            signal_extractor: Layer 2 关键词信号提取器（V2，可选）
            conflict_analyzer: Layer 2 冲突维度分析器（V2，可选）
        """
        self._recommendation_service = recommendation_service
        self._llm_analyzer = llm_analyzer
        self._llm_stance_extractor = llm_stance_extractor
        self._signal_extractor = signal_extractor
        self._conflict_analyzer = conflict_analyzer

    def align(
        self,
        question: str,
        perspectives: list[Perspective],
        driver_bot_id: Optional[str] = None,
        include_recommendation: bool = True,
    ) -> FusionResult:
        """
        执行冲突对齐 - 三层Fallback架构

        Args:
            question: 待对齐的问题
            perspectives: 收集到的视角列表
            driver_bot_id: Driver bot ID
            include_recommendation: 是否生成建议

        Returns:
            FusionResult: 对齐结果
        """
        started_at = datetime.now()

        # 生成 fusion_id
        fusion_id = f"fus-{uuid.uuid4().hex[:12]}"

        logger.info(f"[G2-ALIGN] ========== 开始G2冲突对齐（三层Fallback） ==========")
        logger.info(f"[G2-ALIGN] fusion_id={fusion_id}, question={question[:100]}...")
        logger.info(f"[G2-ALIGN] perspectives count={len(perspectives)}, driver_bot_id={driver_bot_id}")

        # 计算 partial success
        completed_count = sum(1 for p in perspectives if p.status == "completed")
        total_count = len(perspectives)
        partial_success = completed_count > 0 and completed_count < total_count

        # 收集警告
        warnings: list[str] = []
        for p in perspectives:
            if p.status == "timed_out":
                warnings.append(f"participant {p.participant_id} timed out")
            elif p.status == "failed":
                warnings.append(f"participant {p.participant_id} failed")
            elif p.status == "skipped":
                warnings.append(f"participant {p.participant_id} was skipped")

        # 只对成功完成的视角进行冲突和对齐分析
        completed_perspectives = [p for p in perspectives if p.status == "completed"]

        # =====================================
        # 三层Fallback冲突分析
        # =====================================
        analysis_result = self._analyze_conflicts_with_fallback(
            question=question,
            perspectives=completed_perspectives,
        )

        conflicts = analysis_result.conflicts
        alignment_points = analysis_result.alignment_points
        conclusion = analysis_result.conclusion
        structured_conflict_analysis = analysis_result.structured_analysis

        # 生成关键洞察
        key_insights = self._generate_key_insights(
            question=question,
            perspectives=completed_perspectives,
            conflicts=conflicts,
            alignment_points=alignment_points,
        )

        # 生成建议
        recommendation = None
        if include_recommendation:
            recommendation = self._generate_recommendation(
                question=question,
                perspectives=perspectives,
                conflicts=conflicts,
                alignment_points=alignment_points,
                partial_success=partial_success,
                warnings=warnings,
            )

        finished_at = datetime.now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)

        timing = FusionTiming(
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )

        # 结束日志
        logger.info(f"[G2-ALIGN] ========== G2冲突对齐完成 ==========")
        logger.info(f"[G2-ALIGN] 耗时: {duration_ms}ms, 分析来源: {analysis_result.source}")
        logger.info(f"[G2-ALIGN] 结果: conflicts={len(conflicts)}, alignment_points={len(alignment_points)}")
        logger.info(f"[G2-ALIGN] conclusion: {'有' if conclusion else '无'}")

        return FusionResult(
            group_id="",  # 由 GroupFusionService 设置
            fusion_id=fusion_id,
            question=question,
            driver_bot_id=driver_bot_id,
            perspectives=perspectives,
            recommendation=recommendation,
            partial_success=partial_success,
            warnings=warnings,
            errors=[],
            timing=timing,
            fusion_mode="conflict_alignment",
            conflicts=conflicts,
            alignment_points=alignment_points,
            key_insights=key_insights,
            structured_conflict_analysis=structured_conflict_analysis,
            conclusion=conclusion,
            analysis_source=analysis_result.source,
        )

    # =====================================
    # 三层Fallback核心方法
    # =====================================

    def _analyze_conflicts_with_fallback(
        self,
        question: str,
        perspectives: list[Perspective],
    ) -> "AnalysisResult":
        """
        三层Fallback冲突分析

        优先级:
        1. Layer 1: LLM深度研判（如果启用且有结果）
        2. Layer 2: V2结构化分析增强（如果Layer 1失败或禁用）
        3. Layer 3: Legacy规则匹配（兜底）

        Args:
            question: 问题文本
            perspectives: 完成的视角列表

        Returns:
            AnalysisResult: 分析结果
        """
        import time

        metrics = get_g2_metrics()
        llm_analysis = None
        structured_analysis = None

        # =====================================
        # Layer 1: LLM深度研判
        # =====================================
        if self._should_use_layer1_llm():
            logger.info("[G2-FALLBACK] 尝试 Layer 1: LLM深度研判")
            start_time = time.time()
            llm_analysis = self._perform_layer1_analysis(question, perspectives)
            latency_ms = int((time.time() - start_time) * 1000)

            if llm_analysis and llm_analysis.conclusion:
                logger.info(f"[G2-FALLBACK] ✅ Layer 1 成功，使用LLM分析结果, latency={latency_ms}ms")
                metrics.record_analysis("llm", "success", latency_ms)
                result = self._convert_llm_analysis_result(
                    llm_analysis=llm_analysis,
                    question=question,
                    perspectives=perspectives,
                )
                # 记录最终结果
                severity = result.conclusion.overall_severity if result.conclusion else "none"
                metrics.record_final_result("llm", severity)
                return result
            else:
                logger.info("[G2-FALLBACK] Layer 1 无结果，继续fallback")
                metrics.record_analysis("llm", "failed", latency_ms)
                # 记录fallback
                if self._should_use_layer2_v2():
                    metrics.record_fallback("llm", "v2", "no_result")
                else:
                    metrics.record_fallback("llm", "legacy", "no_result")

        # =====================================
        # Layer 2: V2结构化分析增强
        # =====================================
        if self._should_use_layer2_v2():
            logger.info("[G2-FALLBACK] 尝试 Layer 2: V2结构化分析增强")
            start_time = time.time()
            structured_analysis = self._perform_layer2_analysis(question, perspectives)
            latency_ms = int((time.time() - start_time) * 1000)

            if structured_analysis and structured_analysis.overall_conflict_level != "none":
                logger.info(f"[G2-FALLBACK] ✅ Layer 2 成功，使用V2分析结果, latency={latency_ms}ms")
                metrics.record_analysis("v2", "success", latency_ms)
                result = self._convert_v2_analysis_result(
                    structured_analysis=structured_analysis,
                    question=question,
                    perspectives=perspectives,
                    llm_analysis=llm_analysis,
                )
                # 记录最终结果
                severity = result.conclusion.overall_severity if result.conclusion else "none"
                metrics.record_final_result("v2", severity)
                return result
            else:
                logger.info("[G2-FALLBACK] Layer 2 无冲突检测，继续fallback")
                metrics.record_analysis("v2", "failed", latency_ms)
                metrics.record_fallback("v2", "legacy", "none_result")

        # =====================================
        # Layer 3: Legacy规则匹配（兜底）
        # =====================================
        logger.info("[G2-FALLBACK] 使用 Layer 3: Legacy规则匹配")
        start_time = time.time()
        result = self._perform_layer3_analysis(
            question=question,
            perspectives=perspectives,
            structured_analysis=structured_analysis,
        )
        latency_ms = int((time.time() - start_time) * 1000)
        metrics.record_analysis("legacy", "success", latency_ms)

        # 记录最终结果
        severity = result.conclusion.overall_severity if result.conclusion else "none"
        metrics.record_final_result("legacy", severity)

        return result

    def _should_use_layer1_llm(self) -> bool:
        """判断是否应该使用Layer 1 LLM分析"""
        if not FeatureFlags.is_enabled("ENABLE_G2_LLM_CONFLICT_ANALYSIS"):
            return False

        if self._llm_analyzer is None:
            logger.debug("[G2-Layer1] LLM分析器未注入，跳过Layer 1")
            return False

        return True

    def _should_use_layer2_v2(self) -> bool:
        """判断是否应该使用Layer 2 V2分析"""
        if not FeatureFlags.is_enabled("ENABLE_G2_CONFLICT_DIMENSIONS"):
            return False

        if self._conflict_analyzer is None:
            logger.debug("[G2-Layer2] 冲突分析器未注入，跳过Layer 2")
            return False

        return True

    def _perform_layer1_analysis(self, question: str, perspectives: list[Perspective]):
        """执行Layer 1: LLM深度研判"""
        try:
            return self._llm_analyzer.analyze(question=question, perspectives=perspectives)
        except Exception as e:
            logger.warning(f"[G2-Layer1] LLM分析失败: {e}")
            return None

    def _perform_layer2_analysis(self, question: str, perspectives: list[Perspective]):
        """执行Layer 2: V2结构化分析增强"""
        try:
            # 收集所有参与者的立场信号
            all_stance_signals = []

            # 优先使用LLM立场提取器（如果启用）
            if self._llm_stance_extractor and FeatureFlags.is_enabled("ENABLE_G2_LLM_STANCE_EXTRACTION"):
                logger.debug("[G2-Layer2] 使用LLM立场提取器")
                all_signals = self._llm_stance_extractor.extract_all(
                    question=question,
                    perspectives=perspectives,
                )
                for signals in all_signals.values():
                    all_stance_signals.extend(signals)
            elif self._signal_extractor:
                # 使用关键词信号提取器
                logger.debug("[G2-Layer2] 使用关键词信号提取器")
                for perspective in perspectives:
                    signals = self._signal_extractor.extract_from_perspective(
                        perspective=perspective,
                        question=question,
                    )
                    all_stance_signals.extend(signals)

            if not all_stance_signals:
                logger.debug("[G2-Layer2] 未提取到任何立场信号")
                return None

            logger.debug(f"[G2-Layer2] 提取了 {len(all_stance_signals)} 个立场信号")

            # 执行冲突分析
            return self._conflict_analyzer.analyze(all_stance_signals)

        except Exception as e:
            logger.warning(f"[G2-Layer2] V2分析失败: {e}")
            return None

    def _perform_layer3_analysis(
        self,
        question: str,
        perspectives: list[Perspective],
        structured_analysis=None,
    ) -> "AnalysisResult":
        """执行Layer 3: Legacy规则匹配"""
        conflicts = self._extract_conflicts(question, perspectives)
        alignment_points = self._extract_alignment_points(
            question=question,
            perspectives=perspectives,
            conflicts=conflicts,
        )
        conclusion = self._generate_conclusion(
            question=question,
            perspectives=perspectives,
            conflicts=conflicts,
            alignment_points=alignment_points,
            structured_conflict_analysis=structured_analysis,
        )

        return AnalysisResult(
            conflicts=conflicts,
            alignment_points=alignment_points,
            conclusion=conclusion,
            source="legacy",
            structured_analysis=structured_analysis,
        )

    def _convert_llm_analysis_result(
        self,
        llm_analysis,
        question: str,
        perspectives: list[Perspective],
    ) -> "AnalysisResult":
        """将LLM分析结果转换为统一格式"""
        # 转换冲突
        conflicts = []
        for c in llm_analysis.conflicts:
            conflicts.append(FusionConflict(
                parties=c.parties,
                issue=c.issue,
                positions=[f"{p}: {c.conflict_type}" for p in c.parties],
                severity=c.severity,
            ))

        # 转换对齐点
        alignment_points = []
        for a in llm_analysis.alignment_points:
            alignment_points.append(FusionAlignmentPoint(
                summary=a.point,
                participants=a.participants,
            ))

        # 转换结论
        conclusion = None
        if llm_analysis.conclusion:
            from src.domain.models.fusion_conflict_conclusion import ConflictConclusion
            conclusion = ConflictConclusion(
                overall_severity=llm_analysis.conclusion.overall_severity,
                resolution_strategy=llm_analysis.conclusion.resolution_strategy,
                go_no_go=llm_analysis.conclusion.go_no_go,
                priority_actions=llm_analysis.conclusion.priority_actions,
                reasoning=llm_analysis.conclusion.reasoning,
                risks=[],
                conditions=llm_analysis.conclusion.conditions,
            )

        return AnalysisResult(
            conflicts=conflicts,
            alignment_points=alignment_points,
            conclusion=conclusion,
            source="llm",
            llm_analysis=llm_analysis,
        )

    def _convert_v2_analysis_result(
        self,
        structured_analysis,
        question: str,
        perspectives: list[Perspective],
        llm_analysis=None,
    ) -> "AnalysisResult":
        """将V2分析结果转换为统一格式"""
        # 从结构化分析中提取冲突
        conflicts = []
        for pa in structured_analysis.key_conflicts:
            conflicts.append(FusionConflict(
                parties=[pa.participant_a, pa.participant_b],
                issue=pa.rationale or f"在{pa.dimension_id}维度存在冲突",
                positions=[
                    f"{pa.participant_a}: {pa.stance_a.position}",
                    f"{pa.participant_b}: {pa.stance_b.position}",
                ],
                severity=pa.severity or "medium",
            ))

        # 从结构化分析中提取对齐点
        alignment_points = []
        for pa in structured_analysis.key_alignments:
            alignment_points.append(FusionAlignmentPoint(
                summary=pa.rationale or f"在{pa.dimension_id}维度达成共识",
                participants=[pa.participant_a, pa.participant_b],
            ))

        # 生成结论
        conclusion = self._generate_conclusion(
            question=question,
            perspectives=perspectives,
            conflicts=conflicts,
            alignment_points=alignment_points,
            structured_conflict_analysis=structured_analysis,
        )

        return AnalysisResult(
            conflicts=conflicts,
            alignment_points=alignment_points,
            conclusion=conclusion,
            source="v2",
            llm_analysis=llm_analysis,
            structured_analysis=structured_analysis,
        )

    def _extract_conflicts(
        self,
        question: str,
        perspectives: list[Perspective],
    ) -> list[FusionConflict]:
        """
        提取冲突（增强版规则实现）

        检测维度：
        1. 立场冲突（支持 vs 反对）
        2. 关注点冲突（concerns 无交集）
        3. 关键诉求冲突（key_points 矛盾）
        4. 灵活性冲突（一方"不可妥协" vs 另一方诉求）
        5. 问题隐含冲突信号（问题本身暗示冲突主题）
        6. 角色冲突（不同部门/角色立场天然不同）

        Args:
            question: 问题
            perspectives: 完成的视角列表

        Returns:
            list[FusionConflict]: 冲突列表
        """
        conflicts: list[FusionConflict] = []

        if len(perspectives) < 2:
            return conflicts

        # 从问题中提取潜在冲突信号
        question_conflict_signals = self._extract_conflict_signals_from_question(question)

        # 收集所有视角的关键信息
        perspective_info: dict[str, dict] = {}
        for p in perspectives:
            # 提取立场（考虑 flexibility）
            stance = self._extract_stance(p.summary, p.flexibility)
            perspective_info[p.participant_id] = {
                "stance": stance,
                "concerns": p.concerns or [],
                "key_points": p.key_points or [],
                "summary": p.summary,
                "flexibility": p.flexibility,
                "role": p.role,
                "participant_id": p.participant_id,
            }

        # 检测两两之间的冲突
        participant_ids = list(perspective_info.keys())
        for i in range(len(participant_ids)):
            for j in range(i + 1, len(participant_ids)):
                p1_id = participant_ids[i]
                p2_id = participant_ids[j]
                info1 = perspective_info[p1_id]
                info2 = perspective_info[p2_id]

                # 检测冲突
                conflict = self._detect_conflict_between(
                    p1_id=p1_id,
                    info1=info1,
                    p2_id=p2_id,
                    info2=info2,
                    question=question,
                    question_conflict_signals=question_conflict_signals,
                )

                if conflict:
                    conflicts.append(conflict)

        return conflicts

    def _extract_stance(self, summary: str, flexibility: str = None) -> str:
        """
        从摘要和灵活性描述中提取立场

        Args:
            summary: 视角摘要
            flexibility: 参与者的灵活性描述（可选）

        Returns:
            str: 提取的立场
        """
        # 消极立场关键词（优先检测，避免部分匹配问题）
        negative_keywords = ["不可行", "反对", "拒绝", "不通过", "存在风险", "存在问题", "不满足", "不行", "有风险", "不可妥协", "不认可"]
        # 积极立场关键词（注意：检测时要排除已被否定词匹配的部分）
        positive_keywords = ["可行", "同意", "支持", "通过", "接受", "可以", "无问题", "无风险", "认可"]

        combined_text = summary
        if flexibility:
            combined_text += " " + flexibility

        # 先检测消极词
        has_negative = any(kw in combined_text for kw in negative_keywords)

        # 再检测积极词（需要排除被否定前缀修饰的情况）
        has_positive = False
        for kw in positive_keywords:
            if kw in combined_text:
                # 检查是否被否定词前缀修饰
                is_negated = False
                for neg_kw in ["不", "未", "非", "无"]:
                    if neg_kw + kw in combined_text:
                        is_negated = True
                        break
                if not is_negated:
                    has_positive = True
                    break

        if has_positive and not has_negative:
            return "支持"
        elif has_negative and not has_positive:
            return "反对"
        elif has_positive and has_negative:
            return "有条件支持"
        else:
            return "中立"

    def _extract_conflict_signals_from_question(self, question: str) -> list[str]:
        """
        从问题中提取潜在冲突信号（增强版 v2）

        检测维度：
        1. 显式冲突关键词
        2. 隐含冲突句式（"希望...但..."、"建议...但..."等）
        3. 对立维度语义（长期vs短期、安全vs效率等）
        4. 多主体对立（不同团队/角色的诉求对立）

        Args:
            question: 问题文本

        Returns:
            list[str]: 冲突信号列表
        """
        conflict_signals = []
        question_lower = question.lower()

        # ========== 1. 显式冲突关键词 ==========
        explicit_conflict_patterns = [
            ("冲突", "立场分歧"),
            ("分歧", "观点不一致"),
            ("争议", "存在争议"),
            ("僵持", "无法达成一致"),
            ("对立", "利益对立"),
            ("矛盾", "诉求矛盾"),
            ("不认可", "意见不合"),
            ("不同意", "存在异议"),
            ("争论", "存在分歧"),
            ("双方", "两方对峙"),
            ("各执一词", "意见不统一"),
            ("各有诉求", "利益诉求不同"),
            ("难以协调", "协调困难"),
        ]

        for pattern, signal in explicit_conflict_patterns:
            if pattern in question_lower:
                conflict_signals.append(signal)

        # ========== 2. 隐含冲突句式 ==========
        implicit_conflict_patterns = [
            # "希望...但..."模式
            (r"希望.+但.+认为", "诉求对立"),
            (r"希望.+但.+担心", "诉求与风险对立"),
            (r"希望.+但.+认为.+影响", "目标与影响对立"),
            # "建议...但..."模式
            (r"建议.+但.+认为", "建议与观点对立"),
            (r"建议.+但.+担心", "建议与风险对立"),
            # "要求...但..."模式
            (r"要求.+但.+认为", "要求与观点对立"),
            (r"严控.+但.+认为", "控制与观点对立"),
            # "坚持...而..."模式
            (r"坚持.+而.+认为", "立场对立"),
            # "不愿...而..."模式
            (r"不愿.+而.+认为", "立场对立"),
        ]

        import re
        for pattern, signal in implicit_conflict_patterns:
            if re.search(pattern, question_lower):
                conflict_signals.append(signal)

        # ========== 3. 对立维度语义 ==========
        opposition_dimensions = [
            # (正向关键词, 反向关键词, 冲突主题)
            (["长期", "未来", "持续", "稳定", "可持续"], ["短期", "当下", "近期", "眼前", "快速"], "长期利益与短期目标冲突"),
            (["安全", "稳定", "可靠", "风控", "合规"], ["效率", "速度", "快速", "便捷", "流畅"], "安全与效率冲突"),
            (["质量", "品质", "体验", "高标准"], ["速度", "快速", "扩张", "规模"], "质量与速度冲突"),
            (["成本", "省钱", "节约", "控制支出"], ["投入", "投资", "激励", "福利"], "成本控制与投入冲突"),
            (["创新", "敏捷", "灵活", "快速迭代"], ["规范", "流程", "统一", "标准"], "创新与规范冲突"),
            (["用户体验", "体验", "满意度", "客户感受"], ["变现", "营收", "转化", "商业"], "用户体验与商业目标冲突"),
            (["品牌", "调性", "形象", "高端"], ["促销", "低价", "销售", "转化"], "品牌价值与销售目标冲突"),
            (["隐私", "数据保护", "合规", "个人信息"], ["数据利用", "精准营销", "数据分析"], "隐私保护与数据利用冲突"),
        ]

        for pos_keywords, neg_keywords, conflict_theme in opposition_dimensions:
            has_pos = any(kw in question_lower for kw in pos_keywords)
            has_neg = any(kw in question_lower for kw in neg_keywords)
            if has_pos and has_neg:
                conflict_signals.append(conflict_theme)

        # ========== 4. 多主体对立检测 ==========
        # 典型的角色/部门对立模式
        entity_oppositions = [
            # (主体A, 主体B, 冲突主题)
            (["产品", "产品经理", "pm"], ["运营", "运营团队", "ops"], "产品功能与运营诉求冲突"),
            (["技术", "技术团队", "tech", "开发"], ["业务", "业务方", "业务团队"], "技术投入与业务需求冲突"),
            (["风控", "风控团队", "安全", "security"], ["运营", "业务", "产品", "用户"], "风险控制与业务发展冲突"),
            (["品牌", "品牌团队"], ["销售", "销售团队"], "品牌价值与销售目标冲突"),
            (["财务", "财务部门", "finops"], ["hr", "人力资源", "人力"], "成本控制与人力资源冲突"),
            (["合规", "合规团队", "compliance"], ["营销", "市场", "数据", "业务"], "合规约束与业务创新冲突"),
            (["设计", "ux", "体验"], ["营销", "marketing", "广告"], "用户体验与商业变现冲突"),
        ]

        for entities_a, entities_b, conflict_theme in entity_oppositions:
            has_a = any(e in question_lower for e in entities_a)
            has_b = any(e in question_lower for e in entities_b)
            if has_a and has_b:
                conflict_signals.append(conflict_theme)

        # ========== 5. 转折词数量检测 ==========
        # 多个转折词暗示复杂冲突
        transition_words = ["但", "但是", "然而", "却", "不过", "可是", "而"]
        transition_count = sum(1 for tw in transition_words if tw in question_lower)
        if transition_count >= 2:
            conflict_signals.append("多方诉求分歧")

        return list(set(conflict_signals))  # 去重

    def _detect_conflict_between(
        self,
        p1_id: str,
        info1: dict,
        p2_id: str,
        info2: dict,
        question: str,
        question_conflict_signals: list[str] = None,
    ) -> Optional[FusionConflict]:
        """
        检测两个参与者之间是否存在冲突（增强版）

        检测维度：
        1. 立场冲突（支持 vs 反对）
        2. 关注点冲突（concerns 无交集）
        3. 关键诉求冲突（key_points 矛盾）
        4. 灵活性冲突（一方不可妥协 vs 另一方诉求）
        5. 问题隐含冲突（问题本身暗示冲突主题）
        6. 角色/部门天然冲突（不同 role 可能有天然立场差异）

        Args:
            p1_id: 参与者1 ID
            info1: 参与者1 信息
            p2_id: 参与者2 ID
            info2: 参与者2 信息
            question: 问题
            question_conflict_signals: 问题中的冲突信号

        Returns:
            Optional[FusionConflict]: 如果存在冲突，返回冲突对象
        """
        if question_conflict_signals is None:
            question_conflict_signals = []

        stance1 = info1["stance"]
        stance2 = info2["stance"]
        concerns1 = info1["concerns"]
        concerns2 = info2["concerns"]
        key_points1 = info1["key_points"]
        key_points2 = info2["key_points"]
        flexibility1 = info1.get("flexibility")
        flexibility2 = info2.get("flexibility")
        role1 = info1.get("role", "")
        role2 = info2.get("role", "")
        summary1 = info1.get("summary", "")
        summary2 = info2.get("summary", "")

        # 1. 立场完全对立
        if (stance1 == "支持" and stance2 == "反对") or (stance1 == "反对" and stance2 == "支持"):
            return FusionConflict(
                parties=[p1_id, p2_id],
                issue="对问题立场不一致",
                positions=[f"{p1_id}: {stance1}", f"{p2_id}: {stance2}"],
                severity="high",
            )

        # 2. 一方有条件支持，另一方反对
        if (stance1 == "有条件支持" and stance2 == "反对") or (stance1 == "反对" and stance2 == "有条件支持"):
            return FusionConflict(
                parties=[p1_id, p2_id],
                issue="对问题的条件存在分歧",
                positions=[f"{p1_id}: {stance1}", f"{p2_id}: {stance2}"],
                severity="medium",
            )

        # 3. 灵活性冲突：一方不可妥协
        flexibility_conflict = None
        if flexibility1 and "不可妥协" in flexibility1:
            flexibility_conflict = (p1_id, flexibility1)
        elif flexibility2 and "不可妥协" in flexibility2:
            flexibility_conflict = (p2_id, flexibility2)

        if flexibility_conflict and stance1 != stance2:
            return FusionConflict(
                parties=[p1_id, p2_id],
                issue="一方立场不可妥协",
                positions=[f"{p1_id}: {stance1} ({flexibility1 or '可协商'})", f"{p2_id}: {stance2} ({flexibility2 or '可协商'})"],
                severity="high",
            )

        # 4. 双方立场不同，都有顾虑
        if stance1 != stance2 and concerns1 and concerns2:
            return FusionConflict(
                parties=[p1_id, p2_id],
                issue="关注点不同",
                positions=[
                    f"{p1_id}: 关注 {', '.join(concerns1[:2])}",
                    f"{p2_id}: 关注 {', '.join(concerns2[:2])}",
                ],
                severity="medium" if "体验" in str(concerns1 + concerns2) or "风险" in str(concerns1 + concerns2) else "low",
            )

        # 5. 问题隐含冲突信号 => 基于角色推断潜在冲突（增强版 v2）
        # 移除"双方立场都为中立"的限制，只要有冲突信号就检测角色冲突
        if question_conflict_signals:
            # 检测典型的角色冲突模式（扩展版）
            role_conflict_patterns = [
                # (角色1关键词, 角色2关键词, 冲突主题)
                (["pm", "产品", "product"], ["ops", "运营", "marketing", "电商"], "产品功能与运营诉求"),
                (["ux", "设计", "design", "体验"], ["marketing", "营销", "广告", "商业"], "用户体验与商业诉求"),
                (["security", "安全"], ["product", "pm", "dev", "开发"], "安全要求与功能开发"),
                (["compliance", "合规"], ["marketing", "ops", "业务", "数据"], "合规限制与业务诉求"),
                (["tech", "技术", "架构", "开发", "architect"], ["ops", "运营", "业务", "product"], "技术风险与业务机会"),
                (["risk", "风控"], ["product", "业务", "运营", "ux"], "风险控制与业务发展"),
                (["finops", "成本", "财务", "finance"], ["dev", "tech", "hr", "激励", "od"], "成本控制与投入"),
                (["brand", "品牌"], ["sales", "销售", "营销", "marketing"], "品牌价值与销售目标"),
                (["hr", "人力", "人才", "od"], ["finance", "财务", "成本"], "人才激励与成本控制"),
                (["qa", "质量", "测试"], ["ops", "扩张", "业务", "local"], "质量管控与扩张速度"),
                (["ai", "自动化", "智能"], ["service", "客服", "人工"], "自动化与人工服务"),
                (["data", "数据"], ["compliance", "合规", "隐私"], "数据利用与隐私保护"),
                (["local", "本地", "扩张", "retail"], ["qa", "质量", "管控"], "扩张速度与服务质量"),
            ]

            for pattern1, pattern2, conflict_theme in role_conflict_patterns:
                p1_matches = any(kw in p1_id.lower() or kw in role1.lower() for kw in pattern1)
                p2_matches = any(kw in p2_id.lower() or kw in role2.lower() for kw in pattern2)
                # 或者反过来的匹配
                p1_matches_rev = any(kw in p1_id.lower() or kw in role1.lower() for kw in pattern2)
                p2_matches_rev = any(kw in p2_id.lower() or kw in role2.lower() for kw in pattern1)

                if (p1_matches and p2_matches) or (p1_matches_rev and p2_matches_rev):
                    return FusionConflict(
                        parties=[p1_id, p2_id],
                        issue=f"{conflict_theme}存在潜在分歧",
                        positions=[
                            f"{p1_id}: 从{role1 or '其角色'}角度",
                            f"{p2_id}: 从{role2 or '其角色'}角度",
                        ],
                        severity="medium",
                    )

        # 6. 基于问题信号检测诉求对立（增强版 v2）
        if question_conflict_signals:
            # 扩展诉求对立关键词库
            opposition_pairs = [
                # (一方诉求关键词, 另一方诉求关键词)
                (["优先开发", "会员", "积分", "长期价值", "用户粘性"], ["营销工具", "大促", "活动", "短期", "618", "业绩"]),
                (["重构", "技术债务", "稳定", "长远", "架构"], ["新功能", "上线", "抢占市场", "快速", "功能开发"]),
                (["加强验证", "安全", "风控", "人脸识别", "验证措施"], ["用户体验", "转化率", "流失", "便捷", "操作成本"]),
                (["高端", "调性", "品牌形象", "溢价", "定价"], ["促销", "低价", "转化", "销量", "降价"]),
                (["严控", "成本", "节约", "减少", "控制支出"], ["激励", "福利", "士气", "人才", "保留"]),
                (["快速扩张", "更多城市", "抢占市场", "扩张速度"], ["质量", "管控", "口碑", "服务质量", "品控"]),
                (["自动化", "智能客服", "降低成本", "技术"], ["人工", "满意度", "复杂问题", "服务质量"]),
            ]

            for keywords1, keywords2 in opposition_pairs:
                # 合并检查 summary, key_points, concerns
                text1 = f"{summary1} {' '.join(key_points1 or [])} {' '.join(concerns1 or [])}".lower()
                text2 = f"{summary2} {' '.join(key_points2 or [])} {' '.join(concerns2 or [])}".lower()

                p1_has_kw1 = any(kw in text1 for kw in keywords1)
                p2_has_kw2 = any(kw in text2 for kw in keywords2)
                p1_has_kw2 = any(kw in text1 for kw in keywords2)
                p2_has_kw1 = any(kw in text2 for kw in keywords1)

                if (p1_has_kw1 and p2_has_kw2) or (p1_has_kw2 and p2_has_kw1):
                    return FusionConflict(
                        parties=[p1_id, p2_id],
                        issue="关键诉求存在对立",
                        positions=[
                            f"{p1_id}: {', '.join(key_points1[:2]) if key_points1 else '特定诉求'}",
                            f"{p2_id}: {', '.join(key_points2[:2]) if key_points2 else '另一诉求'}",
                        ],
                        severity="medium",
                    )

        # 7. 即使立场都是"中立"，如果关注点不同也可能存在分歧（严重性与信号挂钩）
        if stance1 == "中立" and stance2 == "中立":
            # 检测关注点是否有明显差异
            if concerns1 and concerns2:
                # 检查是否有交集
                concerns1_set = set(c.lower() for c in concerns1)
                concerns2_set = set(c.lower() for c in concerns2)
                if not concerns1_set.intersection(concerns2_set):
                    # 如果有冲突信号，提高严重度
                    severity = "medium" if question_conflict_signals else "low"
                    return FusionConflict(
                        parties=[p1_id, p2_id],
                        issue="关注点存在差异",
                        positions=[
                            f"{p1_id}: 关注 {', '.join(concerns1[:2])}",
                            f"{p2_id}: 关注 {', '.join(concerns2[:2])}",
                        ],
                        severity=severity,
                    )

            # 检测关键诉求是否矛盾
            if key_points1 and key_points2:
                kp1_set = set(kp.lower() for kp in key_points1)
                kp2_set = set(kp.lower() for kp in key_points2)
                # 如果关键诉求完全不同
                if not kp1_set.intersection(kp2_set):
                    severity = "medium" if question_conflict_signals else "low"
                    return FusionConflict(
                        parties=[p1_id, p2_id],
                        issue="关键诉求不同",
                        positions=[
                            f"{p1_id}: {', '.join(key_points1[:2])}",
                            f"{p2_id}: {', '.join(key_points2[:2])}",
                        ],
                        severity=severity,
                    )

        return None

    def _extract_alignment_points(
        self,
        question: str,
        perspectives: list[Perspective],
        conflicts: list[FusionConflict] = None,
    ) -> list[FusionAlignmentPoint]:
        """
        提取对齐点（baseline 规则实现）

        规则：
        1. 相同的立场
        2. 相似的关键点
        3. 共同的顾虑
        4. 不与冲突列表重叠（同一对参与者、同一议题）

        Args:
            question: 问题
            perspectives: 完成的视角列表
            conflicts: 已检测的冲突列表

        Returns:
            list[FusionAlignmentPoint]: 对齐点列表
        """
        if conflicts is None:
            conflicts = []

        alignment_points: list[FusionAlignmentPoint] = []

        if len(perspectives) < 2:
            return alignment_points

        # 构建冲突参与者对集合
        conflict_pairs: set[frozenset] = set()
        for conflict in conflicts:
            # 同一对参与者、同一议题的冲突
            pair = frozenset(conflict.parties)
            conflict_pairs.add(pair)

        # 检测相同立场
        stance_groups: dict[str, list[str]] = {}
        for p in perspectives:
            stance = self._extract_stance(p.summary, p.flexibility)
            if stance not in stance_groups:
                stance_groups[stance] = []
            stance_groups[stance].append(p.participant_id)

        # 同立场 >= 2 个参与者 -> 对齐点（但排除冲突对）
        for stance, participants in stance_groups.items():
            if len(participants) >= 2:
                # 如果立场是"中立"，不自动生成对齐点（中立不代表共识）
                if stance == "中立":
                    continue

                # 检查这些参与者是否在冲突列表中
                pair = frozenset(participants)
                if pair not in conflict_pairs:
                    alignment_points.append(FusionAlignmentPoint(
                        summary=f"{', '.join(participants)} 都持 {stance} 态度",
                        participants=participants,
                    ))

        # 检测共同的关键点
        if len(perspectives) >= 2:
            all_key_points: list[str] = []
            for p in perspectives:
                all_key_points.extend(p.key_points)

            # 简单检测重复出现的关键点
            key_point_counts: dict[str, int] = {}
            for kp in all_key_points:
                # 简化比较
                kp_lower = kp.lower()
                key_point_counts[kp_lower] = key_point_counts.get(kp_lower, 0) + 1

            for kp, count in key_point_counts.items():
                if count >= 2:
                    # 找到共同关注点
                    aligned_participants = [
                        p.participant_id for p in perspectives
                        if any(kp in kp_text.lower() for kp_text in p.key_points)
                    ]
                    if len(aligned_participants) >= 2:
                        pair = frozenset(aligned_participants)
                        # 排除冲突对
                        if pair not in conflict_pairs:
                            alignment_points.append(FusionAlignmentPoint(
                                summary=f"多方都关注: {kp}",
                                participants=aligned_participants,
                            ))

        return alignment_points

    def _generate_key_insights(
        self,
        question: str,
        perspectives: list[Perspective],
        conflicts: list[FusionConflict],
        alignment_points: list[FusionAlignmentPoint],
    ) -> list[str]:
        """
        生成关键洞察

        Args:
            question: 问题
            perspectives: 完成的视角列表
            conflicts: 冲突列表
            alignment_points: 对齐点列表

        Returns:
            list[str]: 关键洞察列表
        """
        insights: list[str] = []

        # 基于冲突数量
        if len(conflicts) == 0 and len(perspectives) >= 2:
            insights.append("各方立场一致")

        if len(conflicts) > 0:
            # 分析冲突严重程度
            high_conflicts = [c for c in conflicts if c.severity == "high"]
            if high_conflicts:
                insights.append("存在主要立场分歧，需要优先协调")
            else:
                insights.append("存在部分分歧，可通过协商解决")

        # 基于对齐点
        if len(alignment_points) >= 2:
            insights.append("多方存在共同关注点，可作为协调基础")
        elif len(alignment_points) == 1:
            insights.append("存在共识基础")

        # 基于视角数量
        if len(perspectives) >= 3:
            insights.append("多方视角已充分收集")

        # 默认洞察
        if not insights:
            insights.append("需要更多视角信息以形成判断")

        return insights[:5]  # 最多返回 5 个洞察

    def _generate_recommendation(
        self,
        question: str,
        perspectives: list[Perspective],
        conflicts: list[FusionConflict],
        alignment_points: list[FusionAlignmentPoint],
        partial_success: bool,
        warnings: list[str],
    ) -> Optional[Recommendation]:
        """
        生成建议

        Args:
            question: 问题
            perspectives: 视角列表
            conflicts: 冲突列表
            alignment_points: 对齐点列表
            partial_success: 是否部分成功
            warnings: 警告列表

        Returns:
            Optional[Recommendation]: 建议对象
        """
        # 如果有 LLM 服务，尝试使用
        if self._recommendation_service is not None:
            try:
                fusion_rec = self._recommendation_service.generate(
                    question=question,
                    driver_bot_id=None,
                    perspectives=perspectives,
                    partial_success=partial_success,
                    warnings=warnings,
                )
                return Recommendation(
                    summary=fusion_rec.summary,
                    decision=fusion_rec.decision.value,
                    risks=fusion_rec.risks,
                    next_actions=fusion_rec.next_actions,
                )
            except Exception:
                # LLM 失败，使用 fallback
                pass

        # Baseline 规则实现
        completed = [p for p in perspectives if p.status == "completed"]
        failed = [p for p in perspectives if p.status in ("failed", "timed_out", "skipped")]

        # 计算平均置信度
        confidences = [p.confidence for p in completed if p.confidence is not None]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.5

        # 风险汇总
        risks: list[str] = []
        for p in failed:
            risks.append(f"{p.participant_id} 视角缺失")
        for c in conflicts:
            if c.severity in ("high", "critical"):
                risks.append(f"存在高严重性冲突: {c.issue}")

        # 下一步行动
        next_actions: list[str] = []
        if conflicts:
            next_actions.append("协调各方立场分歧")
        if alignment_points:
            next_actions.append("基于共识点推进协调")
        if partial_success:
            next_actions.append("补充缺失视角")

        # 决策逻辑
        if len(failed) == 0 and len(conflicts) == 0 and avg_confidence >= 0.7:
            decision = "yes"
            summary = "各方立场一致，建议推进。"
        elif len(conflicts) > 0 and len(alignment_points) > 0:
            decision = "conditional_yes"
            summary = "存在分歧但有共识基础，建议有条件推进。"
        elif len(conflicts) > 0:
            high_conflicts = [c for c in conflicts if c.severity in ("high", "critical")]
            if high_conflicts:
                decision = "no"
                summary = "存在重大立场分歧，建议重新评估。"
            else:
                decision = "conditional_yes"
                summary = "存在部分分歧，建议协调后推进。"
        elif partial_success or len(completed) < 2:
            decision = "needs_more_information"
            summary = "信息不足，建议补充更多视角。"
        else:
            decision = "conditional_yes"
            summary = "基于现有信息建议有条件推进。"

        return Recommendation(
            summary=summary,
            decision=decision,
            risks=risks,
            next_actions=next_actions,
        )

    def _generate_conclusion(
        self,
        question: str,
        perspectives: list[Perspective],
        conflicts: list[FusionConflict],
        alignment_points: list[FusionAlignmentPoint],
        structured_conflict_analysis: Optional[Any] = None,
    ) -> Optional[Any]:
        """
        生成冲突结论

        基于冲突列表、对齐点和结构化分析，生成综合结论。

        Args:
            question: 问题
            perspectives: 完成的视角列表
            conflicts: 冲突列表
            alignment_points: 对齐点列表
            structured_conflict_analysis: 结构化冲突分析（可选）

        Returns:
            Optional[Any]: 冲突结论对象（ConflictConclusion）
        """
        from src.domain.models.fusion_conflict_conclusion import ConflictConclusion

        logger.info(f"[G2-CONCLUSION] 开始生成冲突结论: perspectives={len(perspectives)}, conflicts={len(conflicts)}, alignment_points={len(alignment_points)}")

        # 如果没有冲突，返回简单的积极结论
        if len(conflicts) == 0 and len(perspectives) >= 2:
            logger.info("[G2-CONCLUSION] 无冲突检测到，返回积极结论")
            return ConflictConclusion(
                overall_severity="low",
                resolution_strategy="各方立场一致，无需特别协调，可直接推进。",
                go_no_go="go",
                priority_actions=["直接推进方案执行"],
                reasoning="所有参与方立场一致，没有检测到冲突，对齐点充分，建议推进。",
                risks=[],
                conditions=[],
            )

        # 1. 计算整体严重程度
        overall_severity = self._calculate_overall_severity(conflicts, structured_conflict_analysis)
        logger.info(f"[G2-CONCLUSION] Step1: 计算整体严重程度 = {overall_severity}")

        # 2. 判断推进建议
        go_no_go = self._determine_go_no_go(conflicts, alignment_points, overall_severity)
        logger.info(f"[G2-CONCLUSION] Step2: 判断推进建议 = {go_no_go}")

        # 3. 生成解决策略
        resolution_strategy = self._generate_resolution_strategy(
            conflicts=conflicts,
            alignment_points=alignment_points,
            overall_severity=overall_severity,
            go_no_go=go_no_go,
        )
        logger.info(f"[G2-CONCLUSION] Step3: 生成解决策略 (长度={len(resolution_strategy)})")

        # 4. 提取优先行动项
        priority_actions = self._extract_priority_actions(
            conflicts=conflicts,
            alignment_points=alignment_points,
            go_no_go=go_no_go,
        )
        logger.info(f"[G2-CONCLUSION] Step4: 提取优先行动项 count={len(priority_actions)}")

        # 5. 提取关键风险
        risks = self._extract_conclusion_risks(conflicts, overall_severity)
        logger.info(f"[G2-CONCLUSION] Step5: 提取关键风险 count={len(risks)}")

        # 6. 提取推进条件
        conditions = self._extract_conditions(go_no_go, conflicts, alignment_points)
        logger.info(f"[G2-CONCLUSION] Step6: 提取推进条件 count={len(conditions)}")

        # 7. 生成推理说明
        reasoning = self._generate_reasoning(
            conflicts=conflicts,
            alignment_points=alignment_points,
            overall_severity=overall_severity,
            go_no_go=go_no_go,
        )
        logger.info(f"[G2-CONCLUSION] Step7: 生成推理说明 (长度={len(reasoning)})")

        conclusion = ConflictConclusion(
            overall_severity=overall_severity,
            resolution_strategy=resolution_strategy,
            go_no_go=go_no_go,
            priority_actions=priority_actions,
            reasoning=reasoning,
            risks=risks,
            conditions=conditions,
        )

        logger.info(f"[G2-CONCLUSION] ✅ 冲突结论生成完成: severity={overall_severity}, go_no_go={go_no_go}, actions={len(priority_actions)}")
        return conclusion

    def _calculate_overall_severity(
        self,
        conflicts: list[FusionConflict],
        structured_conflict_analysis: Optional[Any] = None,
    ) -> str:
        """
        计算整体冲突严重程度

        策略：
        1. 如果有结构化分析，优先使用
        2. 否则基于冲突列表的严重程度计算

        Args:
            conflicts: 冲突列表
            structured_conflict_analysis: 结构化冲突分析

        Returns:
            str: 整体严重程度
        """
        # 如果有结构化分析，优先使用（但当返回'none'时，需要检查legacy冲突）
        if structured_conflict_analysis and hasattr(structured_conflict_analysis, "overall_conflict_level"):
            level = structured_conflict_analysis.overall_conflict_level
            logger.info(f"[G2-CONCLUSION] 从结构化分析获取冲突级别: {level}")

            # 如果V2分析未检测到冲突，但legacy有冲突，则使用legacy计算
            if level == "none" and len(conflicts) > 0:
                logger.info(f"[G2-CONCLUSION] V2分析返回'none'但有{len(conflicts)}个legacy冲突，使用legacy计算")
                # 继续执行legacy计算逻辑
            elif level == "none":
                logger.info("[G2-CONCLUSION] V2分析确认无冲突，返回'low'")
                return "low"
            else:
                return level

        # 如果没有冲突
        if len(conflicts) == 0:
            return "low"

        # 统计各严重程度的冲突数量
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for conflict in conflicts:
            severity_counts[conflict.severity] += 1

        # 决策逻辑：
        # - 有任何 critical -> critical
        # - 有 2+ 个 high -> critical
        # - 有 1 个 high -> high
        # - 有 3+ 个 medium -> high
        # - 有 1-2 个 medium -> medium
        # - 全是 low -> low

        if severity_counts["critical"] > 0:
            return "critical"
        elif severity_counts["high"] >= 2:
            return "critical"
        elif severity_counts["high"] == 1:
            return "high"
        elif severity_counts["medium"] >= 3:
            return "high"
        elif severity_counts["medium"] >= 1:
            return "medium"
        else:
            return "low"

    def _determine_go_no_go(
        self,
        conflicts: list[FusionConflict],
        alignment_points: list[FusionAlignmentPoint],
        overall_severity: str,
    ) -> str:
        """
        判断是否推进

        决策逻辑：
        - go: 无冲突或低严重度 + 有对齐点
        - conditional_go: 中等严重度 + 有对齐点，或低严重度但需协商
        - need_discussion: 高严重度 + 有对齐点
        - no_go: critical 严重度或高严重度无对齐点

        Args:
            conflicts: 冲突列表
            alignment_points: 对齐点列表
            overall_severity: 整体严重程度

        Returns:
            str: 推进建议
        """
        has_alignment = len(alignment_points) > 0

        if overall_severity == "low":
            if has_alignment:
                return "go"
            else:
                return "conditional_go" if len(conflicts) > 0 else "go"

        elif overall_severity == "medium":
            return "conditional_go" if has_alignment else "need_discussion"

        elif overall_severity == "high":
            return "need_discussion" if has_alignment else "no_go"

        else:  # critical
            return "no_go"

    def _generate_resolution_strategy(
        self,
        conflicts: list[FusionConflict],
        alignment_points: list[FusionAlignmentPoint],
        overall_severity: str,
        go_no_go: str,
    ) -> str:
        """
        生成解决策略

        基于冲突类型和严重程度，提供针对性的解决策略建议。

        Args:
            conflicts: 冲突列表
            alignment_points: 对齐点列表
            overall_severity: 整体严重程度
            go_no_go: 推进建议

        Returns:
            str: 解决策略
        """
        if go_no_go == "go":
            return "各方立场一致或冲突轻微，无需特别协调，可直接推进方案执行。"

        # 分析冲突类型
        conflict_issues = [c.issue for c in conflicts]
        conflict_severities = [c.severity for c in conflicts]

        strategies = []

        # 根据冲突议题生成策略
        if any("立场" in issue or "对立" in issue for issue in conflict_issues):
            strategies.append("组织协调会议，明确各方核心诉求和底线，寻找折中方案")

        if any("安全" in issue or "合规" in issue for issue in conflict_issues):
            strategies.append("邀请安全和合规团队参与评估，确保方案符合监管要求")

        if any("成本" in issue or "资源" in issue for issue in conflict_issues):
            strategies.append("重新评估投入产出比，制定资源分配方案")

        if any("体验" in issue or "用户" in issue for issue in conflict_issues):
            strategies.append("开展用户调研，用数据驱动决策")

        # 根据严重程度补充策略
        if overall_severity in ("high", "critical"):
            strategies.append("建议升级决策层级，由管理层介入协调")

        # 如果有对齐点，增加积极策略
        if alignment_points:
            strategies.append(f"利用现有的 {len(alignment_points)} 个共识点作为协调基础")

        # 默认策略
        if not strategies:
            strategies.append("建议进一步沟通，明确各方关切，寻找共同利益点")

        return "；".join(strategies[:3]) + "。"

    def _extract_priority_actions(
        self,
        conflicts: list[FusionConflict],
        alignment_points: list[FusionAlignmentPoint],
        go_no_go: str,
    ) -> list[str]:
        """
        提取优先行动项

        基于冲突类型和推进建议，生成具体的行动项列表。

        Args:
            conflicts: 冲突列表
            alignment_points: 对齐点列表
            go_no_go: 推进建议

        Returns:
            list[str]: 优先行动项列表
        """
        actions = []

        if go_no_go == "go":
            actions.append("启动方案执行")
            actions.append("建立定期同步机制")
            return actions[:3]

        elif go_no_go == "conditional_go":
            actions.append("明确推进条件")
            if alignment_points:
                actions.append(f"基于 {len(alignment_points)} 个共识点展开协调")
            actions.append("制定风险评估和应对预案")

        elif go_no_go == "need_discussion":
            actions.append("组织多方协调会议")
            actions.append("准备决策材料供管理层评审")
            if conflicts:
                # 找出最严重的冲突
                high_severity_conflicts = [c for c in conflicts if c.severity in ("high", "critical")]
                if high_severity_conflicts:
                    actions.append(f"优先解决 {len(high_severity_conflicts)} 个高严重性冲突")

        else:  # no_go
            actions.append("暂停方案推进")
            actions.append("重新评估方案可行性")
            actions.append("探索替代方案")

        # 添加冲突相关方的行动项
        if conflicts:
            involved_parties = set()
            for conflict in conflicts[:3]:  # 只考虑前3个冲突
                involved_parties.update(conflict.parties[:2])  # 每个冲突最多2方
            if involved_parties:
                actions.append(f"与 {', '.join(list(involved_parties)[:3])} 进行专项沟通")

        return actions[:5]  # 最多返回5个行动项

    def _extract_conclusion_risks(
        self,
        conflicts: list[FusionConflict],
        overall_severity: str,
    ) -> list[str]:
        """
        提取关键风险

        基于冲突内容和严重程度，识别关键风险。

        Args:
            conflicts: 冲突列表
            overall_severity: 整体严重程度

        Returns:
            list[str]: 关键风险列表
        """
        risks = []

        if overall_severity in ("high", "critical"):
            risks.append("冲突严重程度较高，强行推进可能导致项目失败或重大损失")

        # 分析冲突类型对应的风险
        for conflict in conflicts[:3]:  # 只分析前3个冲突
            if "立场" in conflict.issue or "对立" in conflict.issue:
                risks.append("立场分歧可能导致团队协作效率下降")

            if "安全" in conflict.issue or "合规" in conflict.issue:
                risks.append("安全和合规问题处理不当可能引发监管处罚")

            if "成本" in conflict.issue:
                risks.append("成本争议可能影响项目资源保障")

            if "体验" in conflict.issue:
                risks.append("用户体验问题可能导致客户流失")

        # 去重
        risks = list(dict.fromkeys(risks))

        return risks[:5]  # 最多返回5个风险

    def _extract_conditions(
        self,
        go_no_go: str,
        conflicts: list[FusionConflict],
        alignment_points: list[FusionAlignmentPoint],
    ) -> list[str]:
        """
        提取推进条件

        当推进建议为 conditional_go 时，明确需要满足的条件。

        Args:
            go_no_go: 推进建议
            conflicts: 冲突列表
            alignment_points: 对齐点列表

        Returns:
            list[str]: 推进条件列表
        """
        conditions = []

        if go_no_go != "conditional_go":
            return conditions

        # 基于冲突生成条件
        for conflict in conflicts[:2]:  # 只考虑前2个冲突
            if conflict.severity in ("high", "critical"):
                conditions.append(f"解决关于'{conflict.issue}'的分歧")
            elif conflict.severity == "medium":
                conditions.append(f"就'{conflict.issue}'达成妥协方案")

        # 基于对齐点生成条件
        if alignment_points:
            conditions.append(f"巩固现有的 {len(alignment_points)} 个共识点")

        # 默认条件
        if not conditions:
            conditions.append("所有参与方就核心分歧达成一致")

        return conditions[:3]

    def _generate_reasoning(
        self,
        conflicts: list[FusionConflict],
        alignment_points: list[FusionAlignmentPoint],
        overall_severity: str,
        go_no_go: str,
    ) -> str:
        """
        生成推理说明

        解释为什么得出该结论，提供透明的决策逻辑。

        Args:
            conflicts: 冲突列表
            alignment_points: 对齐点列表
            overall_severity: 整体严重程度
            go_no_go: 推进建议

        Returns:
            str: 推理说明
        """
        conflict_count = len(conflicts)
        alignment_count = len(alignment_points)

        reasoning_parts = []

        # 1. 冲突情况说明
        if conflict_count == 0:
            reasoning_parts.append("未检测到明显冲突")
        else:
            severity_breakdown = {}
            for c in conflicts:
                severity_breakdown[c.severity] = severity_breakdown.get(c.severity, 0) + 1

            breakdown_str = "、".join([
                f"{count}个{level}级别"
                for level, count in sorted(severity_breakdown.items(), key=lambda x: ["critical", "high", "medium", "low"].index(x[0]))
            ])
            reasoning_parts.append(f"共检测到{conflict_count}个冲突（{breakdown_str}）")

        # 2. 对齐点说明
        if alignment_count > 0:
            reasoning_parts.append(f"识别到{alignment_count}个对齐点可作为协调基础")
        else:
            reasoning_parts.append("暂无明显共识点")

        # 3. 严重程度解释
        severity_reasoning = {
            "none": "未检测到明显冲突",
            "low": "冲突轻微，影响有限",
            "medium": "存在一定分歧，需要协调解决",
            "high": "冲突较为严重，需要重点协调",
            "critical": "冲突严重程度极高，存在重大风险",
        }
        reasoning_parts.append(severity_reasoning.get(overall_severity, "冲突程度未知"))

        # 4. 推进建议解释
        go_no_go_reasoning = {
            "go": "具备推进条件，建议直接执行",
            "conditional_go": "有推进可能，但需满足特定条件",
            "need_discussion": "争议较大，需要进一步讨论或升级决策",
            "no_go": "冲突严重，不建议推进",
        }
        reasoning_parts.append(go_no_go_reasoning[go_no_go])

        return "。".join(reasoning_parts) + "。"

    # =====================================
    # V2: 结构化冲突分析方法
    # =====================================

    def _should_use_v2_analysis(self) -> bool:
        """
        判断是否应该使用 V2 结构化分析

        Returns:
            bool: 是否启用 V2 分析
        """
        # 检查 Feature Flags
        if not FeatureFlags.is_enabled("ENABLE_G2_STRUCTURED_STANCE"):
            return False
        if not FeatureFlags.is_enabled("ENABLE_G2_CONFLICT_DIMENSIONS"):
            return False

        # 检查依赖是否注入
        if self._signal_extractor is None or self._conflict_analyzer is None:
            logger.debug(
                "[G2-V2] 信号提取器或冲突分析器未注入，跳过 V2 分析"
            )
            return False

        return True

    def _perform_v2_analysis(
        self,
        question: str,
        perspectives: list[Perspective],
    ):
        """
        执行 V2 结构化分析

        Args:
            question: 问题
            perspectives: 完成的视角列表

        Returns:
            Optional[StructuredConflictAnalysis]: 结构化冲突分析结果
        """
        if not perspectives:
            return None

        try:
            # 1. 提取所有参与者的立场信号
            all_stance_signals = []
            for perspective in perspectives:
                signals = self._signal_extractor.extract_from_perspective(
                    perspective=perspective,
                    question=question,
                )
                all_stance_signals.extend(signals)

            if not all_stance_signals:
                logger.debug("[G2-V2] 未提取到任何立场信号")
                return None

            logger.debug(
                "[G2-V2] 从 %d 个视角中提取了 %d 个立场信号",
                len(perspectives),
                len(all_stance_signals),
            )

            # 2. 执行冲突分析
            analysis = self._conflict_analyzer.analyze(all_stance_signals)

            # 3. 检查是否输出结构化结果
            if not FeatureFlags.is_enabled("ENABLE_G2_STRUCTURED_OUTPUT"):
                logger.debug(
                    "[G2-V2] ENABLE_G2_STRUCTURED_OUTPUT 未开启，不输出结构化结果"
                )
                return None

            return analysis

        except Exception as e:
            logger.warning(
                "[G2-V2] 结构化分析失败，fallback 到 legacy: %s",
                str(e),
            )
            return None


__all__ = [
    "ConflictAlignmentService",
]