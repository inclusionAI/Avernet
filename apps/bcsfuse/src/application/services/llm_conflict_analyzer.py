"""
LLMConflictAnalyzer - LLM驱动的冲突分析器

G2 Conflict Alignment Layer - Phase 1

使用大语言模型进行深度语义分析，识别多方观点中的冲突和对齐点。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

from src.domain.models.llm_conflict_analysis import (
    LLMConflictAnalysis,
    StanceAnalysis,
    LLMConflict,
    LLMAlignmentPoint,
    LLMConclusion,
)
from src.domain.models.fusion_result import Perspective
from src.domain.models.llm_request import LLMRequest
from src.domain.services.llm_provider import LLMProvider
from src.infra.config.feature_flags import FeatureFlags
from src.infra.llm.routing.static_llm_router import StaticLLMRouter

logger = logging.getLogger(__name__)


# =============================================================================
# Prompt模板
# =============================================================================

CONFLICT_ANALYSIS_PROMPT = """分析多方观点，识别冲突和对齐点。

## 问题
{question}

## 观点
{perspectives_formatted}

## 输出格式（仅JSON，无其他文字）
{{
  "stance_analysis": [
    {{
      "participant_id": "ID",
      "stance": "支持/反对/有条件支持/中立",
      "core_demand": "核心诉求",
      "main_concerns": ["顾虑"],
      "flexibility": "不可妥协/可协商/开放态度",
      "rationale": "理由"
    }}
  ],
  "conflicts": [
    {{
      "parties": ["参与方"],
      "conflict_type": "立场对立/诉求冲突/关注点分歧/风险偏好差异",
      "issue": "冲突问题",
      "severity": "critical/high/medium/low",
      "analysis": "分析说明"
    }}
  ],
  "alignment_points": [
    {{
      "participants": ["参与方"],
      "point": "共识点",
      "significance": "意义"
    }}
  ],
  "conclusion": {{
    "overall_severity": "critical/high/medium/low",
    "go_no_go": "go/conditional_go/need_discussion/no_go",
    "resolution_strategy": "解决策略",
    "conditions": ["推进条件"],
    "priority_actions": ["优先行动"],
    "reasoning": "研判理由"
  }}
}}"""


# =============================================================================
# LLMConflictAnalyzer
# =============================================================================


class LLMConflictAnalyzer:
    """
    LLM驱动的冲突分析器

    使用大语言模型进行深度语义理解，识别多方观点中的冲突和对齐点。

    作为G2三层Fallback架构的Layer 1，提供最智能的分析能力。

    Attributes:
        _llm: LLM Provider实例
        _router: LLM路由器
    """

    def __init__(
        self,
        llm_provider: Optional[LLMProvider] = None,
        router: Optional[StaticLLMRouter] = None,
    ):
        """
        初始化分析器

        Args:
            llm_provider: LLM提供者（可选，默认通过路由器获取）
            router: LLM路由器（可选，默认创建新实例）
        """
        self._llm = llm_provider
        self._router = router or StaticLLMRouter()

    def _get_provider(self) -> Optional[LLMProvider]:
        """获取LLM Provider"""
        if self._llm:
            return self._llm

        # 尝试通过依赖注入获取
        try:
            from src.interfaces.api.dependencies.llm_dependencies import get_llm_provider
            return get_llm_provider()
        except Exception:
            return None

    def analyze(
        self,
        question: str,
        perspectives: list[Perspective],
    ) -> Optional[LLMConflictAnalysis]:
        """
        分析多方观点，识别冲突

        Args:
            question: 问题文本
            perspectives: 完成的视角列表

        Returns:
            Optional[LLMConflictAnalysis]: LLM分析结果，失败返回None
        """
        # 检查Feature Flag
        if not FeatureFlags.is_enabled("ENABLE_G2_LLM_CONFLICT_ANALYSIS"):
            logger.debug("[LLM-Conflict] Feature未启用，跳过LLM分析")
            return None

        # 检查输入
        if not perspectives:
            logger.warning("[LLM-Conflict] 无视角数据，跳过分析")
            return None

        start_time = time.time()

        try:
            # 1. 格式化视角信息
            perspectives_formatted = self._format_perspectives(perspectives)

            # 2. 构造Prompt
            prompt = CONFLICT_ANALYSIS_PROMPT.format(
                question=question,
                perspectives_formatted=perspectives_formatted,
            )

            logger.info(f"[LLM-Conflict] 开始LLM冲突分析，参与方数量: {len(perspectives)}")

            # 3. 调用LLM
            response_text = self._call_llm(prompt)

            if not response_text:
                logger.error("[LLM-Conflict] LLM返回空响应")
                return None

            # 4. 解析响应
            analysis = self._parse_response(response_text)

            if analysis:
                latency_ms = int((time.time() - start_time) * 1000)
                analysis.latency_ms = latency_ms

                provider = self._get_provider()
                if provider and hasattr(provider, 'model_name'):
                    analysis.model_used = provider.model_name

                # 记录分析结果摘要
                conflict_summary = f"conflicts={len(analysis.conflicts)}"
                severity_summary = f"severity={analysis.conclusion.overall_severity}" if analysis.conclusion else "no-conclusion"

                logger.info(
                    f"[LLM-Conflict] ✅ 分析完成: {conflict_summary}, {severity_summary}, latency={latency_ms}ms"
                )

            return analysis

        except Exception as e:
            logger.error(f"[LLM-Conflict] ❌ LLM分析失败: {e}")
            return None

    def _call_llm(self, prompt: str) -> Optional[str]:
        """
        调用LLM生成响应

        Args:
            prompt: 输入提示

        Returns:
            Optional[str]: LLM响应文本
        """
        provider = self._get_provider()

        if not provider:
            logger.error("[LLM-Conflict] 无可用的LLM Provider")
            return None

        try:
            from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType

            task_spec = LLMTaskSpec(task_type=TaskType.FUSION_RECOMMENDATION)
            request = LLMRequest(
                task_spec=task_spec,
                user_prompt=prompt,
                temperature=0.3,  # 较低温度保证输出稳定
                max_tokens=8000,  # 增加以容纳完整的冲突分析JSON（GLM-5可能需要更多token）
            )

            # 使用 router 选择正确的物理模型
            model_name = None
            if self._router:
                try:
                    model_profile = self._router.route(task_spec)
                    model_name = model_profile.physical_model_name
                    logger.debug(f"[LLM-Conflict] Router选择模型: {model_name}")
                except Exception as e:
                    logger.warning(f"[LLM-Conflict] Router路由失败: {e}，使用默认模型")

            # 调用 provider，传入模型名称
            response = provider.generate(request, model=model_name)

            if response and response.raw_text:
                return response.raw_text

            return None

        except Exception as e:
            logger.error(f"[LLM-Conflict] LLM调用异常: {e}")
            return None

    def _format_perspectives(self, perspectives: list[Perspective]) -> str:
        """
        格式化视角信息为Prompt文本

        Args:
            perspectives: 视角列表

        Returns:
            str: 格式化后的文本
        """
        parts = []

        for i, p in enumerate(perspectives, 1):
            role_info = f"[{p.role}]" if p.role else ""
            part = f"### 参与方{i}: {p.participant_id} {role_info}\n"
            part += f"**摘要**: {p.summary}\n"

            if p.key_points:
                part += f"**关键点**: {'; '.join(p.key_points)}\n"

            if p.concerns:
                part += f"**顾虑**: {'; '.join(p.concerns)}\n"

            if p.flexibility:
                part += f"**灵活性**: {p.flexibility}\n"

            if p.confidence is not None:
                part += f"**置信度**: {p.confidence:.0%}\n"

            parts.append(part)

        return "\n".join(parts)

    def _parse_response(self, response: str) -> Optional[LLMConflictAnalysis]:
        """
        解析LLM响应

        Args:
            response: LLM返回的文本

        Returns:
            Optional[LLMConflictAnalysis]: 解析后的结果
        """
        try:
            # 记录原始响应以便调试
            logger.info(f"[LLM-Conflict] 原始响应长度: {len(response)}")
            logger.info(f"[LLM-Conflict] 原始响应内容:\n{response}")

            # 提取JSON
            json_str = self._extract_json(response)

            if not json_str:
                logger.error("[LLM-Conflict] 无法从响应中提取JSON")
                return None

            # 尝试直接解析
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError as e:
                logger.warning(f"[LLM-Conflict] JSON解析失败，尝试修复: {e}")

                # 尝试修复不完整的JSON
                repaired_json = self._repair_incomplete_json(json_str)
                if repaired_json:
                    try:
                        data = json.loads(repaired_json)
                        logger.info("[LLM-Conflict] ✅ 使用修复后的JSON继续解析")
                    except json.JSONDecodeError:
                        logger.error("[LLM-Conflict] 修复后的JSON仍无法解析")
                        return None
                else:
                    logger.error("[LLM-Conflict] JSON修复失败")
                    return None

            # 构建结果对象
            stance_analysis = self._parse_stance_analysis(data.get("stance_analysis", []))
            conflicts = self._parse_conflicts(data.get("conflicts", []))
            alignment_points = self._parse_alignment_points(data.get("alignment_points", []))
            conclusion = self._parse_conclusion(data.get("conclusion", {}))

            return LLMConflictAnalysis(
                stance_analysis=stance_analysis,
                conflicts=conflicts,
                alignment_points=alignment_points,
                conclusion=conclusion,
            )

        except json.JSONDecodeError as e:
            logger.error(f"[LLM-Conflict] JSON解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[LLM-Conflict] 响应解析失败: {e}")
            return None

    def _extract_json(self, text: str) -> Optional[str]:
        """
        从文本中提取JSON

        支持以下格式：
        1. ```json ... ``` 代码块
        2. ``` ... ``` 代码块（无json标记）
        3. ```json ... （未闭合的代码块）
        4. 独立的JSON对象

        Args:
            text: 可能包含JSON的文本

        Returns:
            Optional[str]: 提取的JSON字符串
        """
        # 模式1: 尝试匹配```json ... ```块
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', text)
        if json_match:
            result = json_match.group(1).strip()
            logger.info(f"[LLM-Conflict] 提取模式1成功 (```json块): {len(result)} chars")
            return result

        # 模式1.5: 尝试匹配未闭合的```json块（GLM-5输出被截断的情况）
        json_match_unclosed = re.search(r'```json\s*([\s\S]+)$', text)
        if json_match_unclosed:
            result = json_match_unclosed.group(1).strip()
            logger.warning(f"[LLM-Conflict] 检测到未闭合的```json块，提取内容: {len(result)} chars")
            # 后续会通过_repair_incomplete_json修复
            return result

        # 模式2: 尝试匹配``` ... ```块（无json标记）
        code_match = re.search(r'```\s*([\s\S]*?)\s*```', text)
        if code_match:
            candidate = code_match.group(1).strip()
            # 验证是否为JSON
            if candidate.startswith('{') and candidate.endswith('}'):
                logger.info(f"[LLM-Conflict] 提取模式2成功 (```块): {len(candidate)} chars")
                return candidate

        # 模式2.5: 尝试匹配未闭合的```块
        code_match_unclosed = re.search(r'```\s*([\s\S]+)$', text)
        if code_match_unclosed:
            candidate = code_match_unclosed.group(1).strip()
            if candidate.startswith('{'):
                logger.warning(f"[LLM-Conflict] 检测到未闭合的```块，提取内容: {len(candidate)} chars")
                return candidate

        # 模式3: 尝试匹配独立的JSON对象 {...}
        start = text.find('{')
        if start == -1:
            logger.warning("[LLM-Conflict] 响应中未找到 '{' 字符")
            return None

        # 使用平衡括号匹配，但要注意处理字符串内的括号
        depth = 0
        in_string = False
        escape_next = False

        for i, char in enumerate(text[start:]):
            if escape_next:
                escape_next = False
                continue

            if char == '\\' and in_string:
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if not in_string:
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        result = text[start:start + i + 1]
                        logger.info(f"[LLM-Conflict] 提取模式3成功 (平衡括号): {len(result)} chars")
                        return result

        # 模式4: 如果平衡匹配失败，尝试找到最后一个}作为兜底
        last_brace = text.rfind('}')
        if last_brace > start:
            result = text[start:last_brace + 1]
            logger.warning(f"[LLM-Conflict] 使用兜底模式提取 (到最后}}): {len(result)} chars")
            # 尝试验证是否为有效JSON
            try:
                json.loads(result)
                return result
            except json.JSONDecodeError:
                pass

        # 模式5: 如果连}都没有，返回从{开始到结尾的所有内容（极端截断情况）
        result = text[start:]
        logger.warning(f"[LLM-Conflict] 响应被严重截断，提取从{{开始的所有内容: {len(result)} chars")
        return result

    def _repair_incomplete_json(self, incomplete_json: str) -> Optional[str]:
        """
        尝试修复不完整的JSON

        当GLM-5的响应被截断时（比如max_tokens限制），JSON可能不完整。
        这个方法尝试通过补全括号和基本结构来修复。

        Args:
            incomplete_json: 不完整的JSON字符串

        Returns:
            Optional[str]: 修复后的JSON字符串，失败返回None
        """
        try:
            # 1. 先尝试直接解析
            json.loads(incomplete_json)
            return incomplete_json
        except json.JSONDecodeError:
            pass

        # 2. 检查并补全缺失的括号
        open_braces = incomplete_json.count('{') - incomplete_json.count('}')
        open_brackets = incomplete_json.count('[') - incomplete_json.count(']')

        if open_braces > 0 or open_brackets > 0:
            logger.info(f"[LLM-Conflict] 检测到不完整JSON: 缺失 {open_braces} 个 }}, {open_brackets} 个 ]")

            # 添加缺失的闭合括号
            repaired = incomplete_json.rstrip()

            # 关闭未闭合的字符串
            if repaired.count('"') % 2 == 1:
                repaired += '"'
                logger.info("[LLM-Conflict] 添加了闭合引号")

            # 关闭未闭合的数组和对象（从内到外）
            # 先关闭数组，再关闭对象
            repaired += ']' * open_brackets + '}' * open_braces

            logger.info(f"[LLM-Conflict] 尝试修复JSON，添加 {open_brackets} 个 ], {open_braces} 个 }}")

            # 3. 验证修复后的JSON
            try:
                json.loads(repaired)
                logger.info("[LLM-Conflict] ✅ JSON修复成功")
                return repaired
            except json.JSONDecodeError as e:
                logger.warning(f"[LLM-Conflict] JSON修复失败: {e}")

                # 4. 尝试更激进的修复：截断到最后一个完整的元素
                return self._truncate_to_last_complete_element(incomplete_json)

        return None

    def _truncate_to_last_complete_element(self, incomplete_json: str) -> Optional[str]:
        """
        截断到最后一个完整的JSON元素

        当无法修复整个JSON时，尝试保留完整的部分。
        对于stance_analysis数组，保留前面完整的元素。

        Args:
            incomplete_json: 不完整的JSON字符串

        Returns:
            Optional[str]: 截断后的有效JSON
        """
        try:
            # 找到stance_analysis数组
            stance_match = re.search(r'"stance_analysis"\s*:\s*\[', incomplete_json)
            if not stance_match:
                return None

            array_start = stance_match.end()

            # 找到最后一个完整的对象元素
            depth = 1  # 数组已经开启了
            obj_depth = 0
            last_complete_obj_end = array_start
            in_string = False
            escape_next = False

            i = array_start
            while i < len(incomplete_json) and depth > 0:
                char = incomplete_json[i]

                if escape_next:
                    escape_next = False
                    i += 1
                    continue

                if char == '\\' and in_string:
                    escape_next = True
                    i += 1
                    continue

                if char == '"' and not escape_next:
                    in_string = not in_string
                    i += 1
                    continue

                if not in_string:
                    if char == '{':
                        obj_depth += 1
                    elif char == '}':
                        obj_depth -= 1
                        if obj_depth == 0:
                            # 找到一个完整的对象
                            last_complete_obj_end = i + 1
                    elif char == '[':
                        depth += 1
                    elif char == ']':
                        depth -= 1

                i += 1

            # 构造截断后的JSON
            if last_complete_obj_end > array_start:
                truncated = (
                    incomplete_json[:array_start] +
                    incomplete_json[array_start:last_complete_obj_end] +
                    "]}"  # 关闭数组和对象
                )

                logger.info(f"[LLM-Conflict] 截断到最后一个完整元素，保留 {last_complete_obj_end - array_start} 字符")

                try:
                    json.loads(truncated)
                    logger.info("[LLM-Conflict] ✅ 截断修复成功")
                    return truncated
                except json.JSONDecodeError:
                    pass

            return None

        except Exception as e:
            logger.error(f"[LLM-Conflict] 截断修复失败: {e}")
            return None

    def _parse_stance_analysis(self, data: list) -> list[StanceAnalysis]:
        """解析立场分析列表"""
        result = []
        for item in data:
            try:
                stance = StanceAnalysis(
                    participant_id=item.get("participant_id", "unknown"),
                    stance=item.get("stance", "中立"),
                    core_demand=item.get("core_demand", ""),
                    main_concerns=item.get("main_concerns", []),
                    flexibility=item.get("flexibility", "可协商"),
                    rationale=item.get("rationale", ""),
                )
                result.append(stance)
            except Exception as e:
                logger.warning(f"[LLM-Conflict] 解析立场失败: {e}, data={item}")
        return result

    def _parse_conflicts(self, data: list) -> list[LLMConflict]:
        """解析冲突列表"""
        result = []
        for item in data:
            try:
                conflict = LLMConflict(
                    parties=item.get("parties", []),
                    conflict_type=item.get("conflict_type", "关注点分歧"),
                    issue=item.get("issue", ""),
                    severity=item.get("severity", "medium"),
                    analysis=item.get("analysis", ""),
                )
                result.append(conflict)
            except Exception as e:
                logger.warning(f"[LLM-Conflict] 解析冲突失败: {e}, data={item}")
        return result

    def _parse_alignment_points(self, data: list) -> list[LLMAlignmentPoint]:
        """解析对齐点列表"""
        result = []
        for item in data:
            try:
                alignment = LLMAlignmentPoint(
                    participants=item.get("participants", []),
                    point=item.get("point", ""),
                    significance=item.get("significance", ""),
                )
                result.append(alignment)
            except Exception as e:
                logger.warning(f"[LLM-Conflict] 解析对齐点失败: {e}, data={item}")
        return result

    def _parse_conclusion(self, data: dict) -> Optional[LLMConclusion]:
        """解析结论"""
        if not data:
            return None

        try:
            return LLMConclusion(
                overall_severity=data.get("overall_severity", "medium"),
                go_no_go=data.get("go_no_go", "need_discussion"),
                resolution_strategy=data.get("resolution_strategy", ""),
                conditions=data.get("conditions", []),
                priority_actions=data.get("priority_actions", []),
                reasoning=data.get("reasoning", ""),
            )
        except Exception as e:
            logger.warning(f"[LLM-Conflict] 解析结论失败: {e}, data={data}")
            return None


__all__ = [
    "LLMConflictAnalyzer",
    "CONFLICT_ANALYSIS_PROMPT",
]