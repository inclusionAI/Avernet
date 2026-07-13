"""
LLMStanceExtractor - LLM驱动的立场提取器

G2 Conflict Alignment Layer - Phase 2

使用大语言模型从视角中提取立场信号，用于增强V2结构化分析。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from src.domain.models.stance_signal import StanceSignal
from src.domain.models.fusion_result import Perspective
from src.domain.models.llm_request import LLMRequest
from src.domain.services.llm_provider import LLMProvider
from src.infra.config.feature_flags import FeatureFlags
from src.infra.llm.routing.static_llm_router import StaticLLMRouter

logger = logging.getLogger(__name__)


def _get_max_workers() -> int:
    """从配置获取最大并发数，默认5"""
    try:
        value = os.environ.get("LLM_PARALLEL_MAX_WORKERS", "5")
        return int(value)
    except (ValueError, TypeError):
        return 5


# =============================================================================
# Prompt模板
# =============================================================================

STANCE_EXTRACTION_PROMPT = """你是一个立场分析专家。请分析以下参与者在该问题上的立场。

## 问题
{question}

## 参与者观点
{perspective_formatted}

## 分析任务
1. 识别该参与者在问题中涉及的核心冲突维度（如：速度vs质量、成本vs安全、短期vs长期等）
2. 判断该参与者在每个维度上的立场倾向
3. 评估立场的强度和置信度

## 立场定义
- axis_a: 倾向维度的一端（如"速度优先"）
- axis_b: 倾向维度的另一端（如"质量优先"）
- balanced: 两端兼顾，寻求平衡
- neutral: 无明显倾向或立场不明确

## 输出格式
请严格按照JSON格式输出：

{{
  "dimension_id": "冲突维度标识（英文下划线命名，如speed_vs_quality）",
  "dimension_name": "维度中文名称",
  "position": "axis_a/axis_b/balanced/neutral",
  "strength": 0.0到1.0之间的数值,
  "confidence": 0.0到1.0之间的数值,
  "rationale": "立场判断的理由",
  "evidence": ["支持该判断的证据1", "证据2"]
}}

如果涉及多个维度，请输出数组格式。

请确保分析客观、有据。只输出JSON，不要有任何其他内容。"""


# =============================================================================
# LLMStanceExtractor
# =============================================================================


class LLMStanceExtractor:
    """
    LLM驱动的立场提取器

    使用大语言模型从视角中提取立场信号，支持动态维度识别。

    作为G2三层Fallback架构的Layer 2增强组件。

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
        初始化提取器

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

    def extract(
        self,
        question: str,
        perspective: Perspective,
    ) -> list[StanceSignal]:
        """
        从单个视角中提取立场信号

        Args:
            question: 问题文本
            perspective: 视角对象

        Returns:
            list[StanceSignal]: 立场信号列表，失败返回空列表
        """
        # 检查Feature Flag
        if not FeatureFlags.is_enabled("ENABLE_G2_LLM_STANCE_EXTRACTION"):
            logger.debug("[LLM-Stance] Feature未启用，跳过LLM立场提取")
            return []

        start_time = time.time()

        try:
            # 1. 格式化视角信息
            perspective_formatted = self._format_perspective(perspective)

            # 2. 构造Prompt
            prompt = STANCE_EXTRACTION_PROMPT.format(
                question=question,
                perspective_formatted=perspective_formatted,
            )

            logger.debug(f"[LLM-Stance] 开始提取立场: participant={perspective.participant_id}")

            # 3. 调用LLM
            response_text = self._call_llm(prompt)

            if not response_text:
                logger.warning("[LLM-Stance] LLM返回空响应")
                return []

            # 4. 解析响应
            signals = self._parse_response(response_text, perspective.participant_id)

            if signals:
                latency_ms = int((time.time() - start_time) * 1000)

                logger.info(
                    f"[LLM-Stance] ✅ 提取完成: participant={perspective.participant_id}, "
                    f"dimensions={len(signals)}, latency={latency_ms}ms"
                )

            return signals

        except Exception as e:
            logger.error(f"[LLM-Stance] ❌ 立场提取失败: {e}")
            return []

    def extract_all(
        self,
        question: str,
        perspectives: list[Perspective],
        max_workers: Optional[int] = None,
    ) -> dict[str, list[StanceSignal]]:
        """
        批量从多个视角中提取立场信号（并行优化版本）

        使用 ThreadPoolExecutor 并行调用 LLM，显著降低批量提取的延迟。

        性能对比：
        - 串行模式：N × 单次延迟（N个视角按顺序处理）
        - 并行模式：单次延迟 + 少量开销（N个视角同时处理）

        Args:
            question: 问题文本
            perspectives: 视角列表
            max_workers: 最大并发数，默认从环境变量 LLM_PARALLEL_MAX_WORKERS 读取，默认5

        Returns:
            dict[str, list[StanceSignal]]: 参与者ID -> 立场信号列表的映射
        """
        if not perspectives:
            return {}

        # 从配置获取默认并发数
        if max_workers is None:
            max_workers = _get_max_workers()

        # 单个视角直接处理，避免线程池开销
        if len(perspectives) == 1:
            return {perspectives[0].participant_id: self.extract(question, perspectives[0])}

        start_time = time.time()
        result = {}

        # 并行提取配置
        actual_workers = min(len(perspectives), max_workers)
        logger.info(
            f"[LLM-Stance] 开始并行提取: perspectives={len(perspectives)}, "
            f"max_workers={actual_workers}"
        )

        try:
            with ThreadPoolExecutor(max_workers=actual_workers) as executor:
                # 提交所有任务
                future_to_perspective = {
                    executor.submit(self.extract, question, p): p
                    for p in perspectives
                }

                # 收集结果
                success_count = 0
                failed_count = 0

                for future in as_completed(future_to_perspective):
                    perspective = future_to_perspective[future]
                    participant_id = perspective.participant_id

                    try:
                        signals = future.result()
                        result[participant_id] = signals
                        if signals:
                            success_count += 1
                        else:
                            failed_count += 1
                    except Exception as e:
                        logger.error(
                            f"[LLM-Stance] 视角处理异常: participant={participant_id}, error={e}"
                        )
                        result[participant_id] = []
                        failed_count += 1

            # 记录并行性能指标
            total_latency_ms = int((time.time() - start_time) * 1000)
            total_signals = sum(len(s) for s in result.values())

            logger.info(
                f"[LLM-Stance] ✅ 并行提取完成: total_latency={total_latency_ms}ms, "
                f"success={success_count}/{len(perspectives)}, "
                f"total_signals={total_signals}"
            )

            return result

        except Exception as e:
            # 并行执行失败的降级处理：回退到串行模式
            logger.warning(
                f"[LLM-Stance] 并行执行失败，回退到串行模式: {e}"
            )
            return self._extract_all_serial(question, perspectives)

    def _extract_all_serial(
        self,
        question: str,
        perspectives: list[Perspective],
    ) -> dict[str, list[StanceSignal]]:
        """
        串行批量提取（降级方案）

        当并行执行失败时，回退到原始的串行处理模式。

        Args:
            question: 问题文本
            perspectives: 视角列表

        Returns:
            dict[str, list[StanceSignal]]: 参与者ID -> 立场信号列表的映射
        """
        result = {}

        for perspective in perspectives:
            signals = self.extract(question, perspective)
            result[perspective.participant_id] = signals

        return result

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
            logger.error("[LLM-Stance] 无可用的LLM Provider")
            return None

        try:
            from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType

            request = LLMRequest(
                task_spec=LLMTaskSpec(task_type=TaskType.FUSION_RECOMMENDATION),
                user_prompt=prompt,
                temperature=0.3,  # 较低温度保证输出稳定
                max_tokens=1000,
            )

            response = provider.generate(request)

            if response and response.raw_text:
                return response.raw_text

            return None

        except Exception as e:
            logger.error(f"[LLM-Stance] LLM调用异常: {e}")
            return None

    def _format_perspective(self, perspective: Perspective) -> str:
        """
        格式化视角信息为Prompt文本

        Args:
            perspective: 视角对象

        Returns:
            str: 格式化后的文本
        """
        parts = []

        role_info = f"[{perspective.role}]" if perspective.role else ""
        parts.append(f"角色: {perspective.participant_id} {role_info}")
        parts.append(f"摘要: {perspective.summary}")

        if perspective.key_points:
            parts.append(f"关键点: {'; '.join(perspective.key_points)}")

        if perspective.concerns:
            parts.append(f"顾虑: {'; '.join(perspective.concerns)}")

        if perspective.flexibility:
            parts.append(f"灵活性: {perspective.flexibility}")

        if perspective.confidence is not None:
            parts.append(f"置信度: {perspective.confidence:.0%}")

        return "\n".join(parts)

    def _parse_response(
        self,
        response: str,
        participant_id: str,
    ) -> list[StanceSignal]:
        """
        解析LLM响应

        Args:
            response: LLM返回的文本
            participant_id: 参与者ID

        Returns:
            list[StanceSignal]: 解析后的立场信号列表
        """
        try:
            # 提取JSON
            json_str = self._extract_json(response)

            if not json_str:
                logger.error("[LLM-Stance] 无法从响应中提取JSON")
                return []

            data = json.loads(json_str)

            # 处理单个对象和数组两种格式
            if isinstance(data, dict):
                data = [data]

            # 构建结果
            signals = []
            for item in data:
                signal = self._parse_single_signal(item, participant_id)
                if signal:
                    signals.append(signal)

            return signals

        except json.JSONDecodeError as e:
            logger.error(f"[LLM-Stance] JSON解析失败: {e}")
            return []
        except Exception as e:
            logger.error(f"[LLM-Stance] 响应解析失败: {e}")
            return []

    def _extract_json(self, text: str) -> Optional[str]:
        """
        从文本中提取JSON

        支持以下格式：
        1. ```json ... ``` 代码块
        2. 独立的JSON对象或数组

        Args:
            text: 可能包含JSON的文本

        Returns:
            Optional[str]: 提取的JSON字符串
        """
        # 尝试匹配```json ... ```块
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', text)
        if json_match:
            return json_match.group(1).strip()

        # 查找第一个 { 或 [ 来确定JSON起始位置
        obj_start = text.find('{')
        array_start = text.find('[')

        # 确定JSON起始位置（优先选择最先出现的）
        if obj_start == -1 and array_start == -1:
            return None

        # 优先选择对象（因为对象可能包含数组，但数组不应该被单独提取）
        if obj_start != -1 and (array_start == -1 or obj_start < array_start):
            # 提取对象 {...}
            depth = 0
            for i, char in enumerate(text[obj_start:]):
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        return text[obj_start:obj_start + i + 1]
        elif array_start != -1:
            # 提取数组 [...]
            depth = 0
            for i, char in enumerate(text[array_start:]):
                if char == '[':
                    depth += 1
                elif char == ']':
                    depth -= 1
                    if depth == 0:
                        return text[array_start:array_start + i + 1]

        return None

    def _parse_single_signal(
        self,
        item: dict,
        participant_id: str,
    ) -> Optional[StanceSignal]:
        """解析单个立场信号"""
        try:
            # 验证item是字典类型
            if not isinstance(item, dict):
                logger.warning(f"[LLM-Stance] 立场信号数据格式错误，期望dict，实际为{type(item).__name__}: {item}")
                return None

            return StanceSignal(
                participant_id=participant_id,
                dimension_id=item.get("dimension_id", "unknown"),
                position=item.get("position", "neutral"),
                strength=float(item.get("strength", 0.0)),
                confidence=float(item.get("confidence", 0.0)),
                evidence=item.get("evidence", []),
                rationale=item.get("rationale"),
            )
        except Exception as e:
            logger.warning(f"[LLM-Stance] 解析立场信号失败: {e}, item={item}")
            return None


__all__ = [
    "LLMStanceExtractor",
    "STANCE_EXTRACTION_PROMPT",
]