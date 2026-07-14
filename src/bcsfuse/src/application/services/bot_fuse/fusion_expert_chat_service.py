"""
FusionExpertChatService

G9 Profile Fusion 模式的专家对话服务。

职责：
- 构建 System/User Prompt（总-分-总分析框架）
- 模型选择链（REASONING_MODEL → BALANCED_MODEL → FAST_MODEL）
- 指数退避重试（最多3次）
- 结果构建（成功/错误）
- 对话轮次记录和持久化

G9 三次模型调用：
1. GroupContextService - 会话总结（改写问题+摘要）
2. ProfileMergeService - Profile 融合
3. FusionExpertChatService - Prompt构建 + LLM调用 + 结果构建

存储集成：
- 注入 FusedProfileStorageService 实现对话轮次记录
- 与 ProfileMergeService 共享存储，追加对话历史
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from src.domain.models.profile_fusion import ConversationTurn
from src.domain.enums.fuse_enums import FusionStatus
from src.utils.env_utils import get_server_ip

if TYPE_CHECKING:
    from src.domain.models.profile_fusion import FusedProfile, GroupConversationSummary
    from src.domain.models.fusion_request import FusionRequest
    from src.domain.models.fusion_result import FusionResult, Recommendation, FusionTiming
    from src.domain.models.profile_fusion.fused_profile import ProfileFusionResult
    from src.application.services.llm_gateway_service import LLMGatewayService
    from application.services.bot_fuse.fused_profile_storage_service import FusedProfileStorageService

logger = logging.getLogger(__name__)


# 会话背景模板
CONTEXT_SUMMARY_TEMPLATE = """【会话背景】
以下是群组近期的对话摘要，帮助你理解问题的上下文：

{context_summary}

---
"""

# 待分析问题模板（仅有原始问题）
QUESTION_ONLY_TEMPLATE = """【待分析问题】
{original_question}
"""

# 待分析问题模板（有原始问题和改写问题）
QUESTION_WITH_REWRITE_TEMPLATE = """【待分析问题】
原始问题: {original_question}
改写问题（补充了上下文）: {rewritten_question}

请针对改写后的问题进行分析，同时参考原始问题的表述。
"""

# Profile Fusion User Prompt 模板（总-分-总结构）
PROFILE_FUSION_USER_PROMPT_TEMPLATE = """
---

## 一、问题拆解与专家分工

**要求**：
1. 识别问题的核心关注点
2. 拆解需要分析的关键维度
3. 根据你的专业能力（参见人格画像中的各专家信息），分配每个维度由哪些专家视角负责分析

---

## 二、各专家分维度分析

**要求**：
- 针对每个维度，由对应的专家视角进行**深入的独立分析**
- 充分体现该专家的专业特色、技术视角和经验积累
- 分析要有深度，不要泛泛而谈
- 可以保留各专家的表达风格

---

## 三、融合视角分析

**要求**：
- 站在团队整体视角，综合各专家的分析结果
- 找出不同专家观点的交叉点和互补性
- 提出单一专家难以发现的创新思路
- 形成跨领域、多维度的综合见解

---

## 四、综合结论与建议

**要求**：
1. **观点汇总**：提炼各专家分析的核心观点
2. **综合解答**：针对原问题给出完整、统一的解答
3. **意见与建议**：你自己的专业意见和具体可操作的建议
4. **风险提示**：识别潜在风险或需要进一步关注的问题

---

【注意事项】
- 总-分-总结构是必须的，请严格按照四个部分输出
- 第二部分的分析要充分展开，体现专业性
- 第三部分需要体现跨领域融合的优势
- 第四部分需要有你自己独立的判断和建议，而非简单汇总
"""


class FusionExpertChatService:
    """
    融合专家对话服务

    负责 G9 模式的 Prompt 构建、LLM 调用和结果构建。

    存储集成：
    - 注入 storage_service 实现对话轮次记录
    - 与 ProfileMergeService 共享存储，追加对话历史
    """

    def __init__(
        self,
        llm_gateway: Optional["LLMGatewayService"] = None,
        storage_service: Optional["FusedProfileStorageService"] = None,
    ):
        """
        初始化服务

        Args:
            llm_gateway: LLM Gateway 服务
            storage_service: 存储服务（用于记录对话轮次）
        """
        self._llm_gateway = llm_gateway
        self._storage_service = storage_service
        if storage_service:
            logger.info("[FusionChat] 已注入存储服务，支持对话轮次记录")

    def set_llm_gateway(self, llm_gateway: "LLMGatewayService") -> None:
        """
        设置 LLM Gateway 服务

        Args:
            llm_gateway: LLM Gateway 服务实例
        """
        self._llm_gateway = llm_gateway
        logger.info("[FusionChat] 已设置 LLM Gateway 服务")

    def set_storage_service(self, storage_service: "FusedProfileStorageService") -> None:
        """
        设置存储服务

        Args:
            storage_service: 存储服务实例
        """
        self._storage_service = storage_service
        logger.info("[FusionChat] 已设置存储服务")

    # =========================================================================
    # 对话轮次记录
    # =========================================================================

    def record_conversation_turn(
        self,
        fusion_id: str,
        question: str,
        answer: str,
        sender_id: Optional[str] = None,
        sender_name: Optional[str] = None,
        response_ms: Optional[int] = None,
        question_token: Optional[int] = None,
        response_token: Optional[int] = None,
        original_question: Optional[str] = None,
        rewritten_question: Optional[str] = None,
        context_summary: Optional[str] = None,
    ) -> None:
        """
        记录对话轮次到存储

        Args:
            fusion_id: 融合 ID
            question: 用户问题
            answer: LLM 回答
            sender_id: 发送者 ID
            sender_name: 发送者名称
            response_ms: 响应时间（毫秒）
            question_token: 问题 token 数
            response_token: 回答 token 数
            original_question: 原始问题（改写前）
            rewritten_question: 改写后的问题（补充了上下文）
            context_summary: 会话上下文摘要
        """
        if not self._storage_service:
            logger.warning("[FusionChat] 存储服务未设置，跳过对话轮次记录")
            return

        turn = ConversationTurn(
            turn_index=0,  # 会被 storage_service 自动设置
            question=question,
            original_question=original_question,
            rewritten_question=rewritten_question,
            context_summary=context_summary,
            sender_id=sender_id,
            sender_name=sender_name,
            answer_content=answer,
            answer_response_ms=response_ms,
            question_token=question_token,
            response_token=response_token,
            server_ip=get_server_ip(),
        )

        try:
            self._storage_service.append_conversation_turn(fusion_id, turn)
            logger.info("[FusionChat] 已记录对话轮次: fusion_id=%s", fusion_id)
        except Exception as e:
            logger.error("[FusionChat] 记录对话轮次失败: fusion_id=%s, error=%s", fusion_id, str(e))

    # =========================================================================
    # Prompt 构建（原 CustomAgentService）
    # =========================================================================

    def build_prompts(
        self,
        fused_profile: "FusedProfile",
        original_question: str,
        rewritten_question: Optional[str] = None,
        context_summary: Optional[str] = None,
    ) -> tuple[str, str]:
        """
        构建 Bot 融合对话的 System Prompt 和 User Prompt

        Args:
            fused_profile: 融合后的 Profile
            original_question: 原始问题
            rewritten_question: 改写后的问题（可选，补充了上下文）
            context_summary: 会话上下文摘要（可选）

        Returns:
            tuple[str, str]: (system_prompt, user_prompt)
        """
        step_start = time.time()
        logger.info("[FusionChat] ========== 开始构建 Prompt ==========")

        # System Prompt 直接使用融合后的 Profile（已包含所有专家信息）
        system_prompt = fused_profile.to_system_prompt()

        logger.info("[FusionChat] System Prompt 长度: %d chars (%.1f KB)",
                   len(system_prompt), len(system_prompt) / 1024)

        # 构建 User Prompt：会话背景 + 问题 + 分析框架
        user_prompt_parts = []

        # 1. 如果有会话摘要，先添加会话背景
        if context_summary:
            user_prompt_parts.append(
                CONTEXT_SUMMARY_TEMPLATE.format(context_summary=context_summary)
            )

        # 2. 问题部分（优先使用改写后的问题）
        if rewritten_question and rewritten_question != original_question:
            user_prompt_parts.append(
                QUESTION_WITH_REWRITE_TEMPLATE.format(
                    original_question=original_question,
                    rewritten_question=rewritten_question,
                )
            )
        else:
            user_prompt_parts.append(
                QUESTION_ONLY_TEMPLATE.format(original_question=original_question)
            )

        # 3. 分析框架（总-分-总结构）
        user_prompt_parts.append(PROFILE_FUSION_USER_PROMPT_TEMPLATE)

        user_prompt = "\n".join(user_prompt_parts)

        elapsed = time.time() - step_start
        logger.info("[FusionChat] Prompt 构建完成: 耗时=%.3fs", elapsed)
        logger.info("[FusionChat] User Prompt 长度: %d chars (%.1f KB)",
                   len(user_prompt), len(user_prompt) / 1024)
        if context_summary:
            logger.info("[FusionChat] 会话摘要长度: %d chars", len(context_summary))
        if rewritten_question and rewritten_question != original_question:
            logger.info("[FusionChat] 问题已改写: '%s...' -> '%s...'",
                       original_question[:30], rewritten_question[:30])

        return system_prompt, user_prompt

    # =========================================================================
    # LLM 调用（原 GroupFusionService._call_g9_llm）
    # =========================================================================

    def call_with_retry(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout_ms: int,
    ) -> tuple["Recommendation | None", list[str], float, dict[str, int] | None]:
        """
        带重试的 LLM 调用

        LLM 稳定性方案:
        1. 模型选择链: REASONING_MODEL → BALANCED_MODEL → FAST_MODEL
        2. 指数退避重试: 每个模型失败后等待 1s, 2s 重试
        3. 最多重试 3 次

        Args:
            system_prompt: System Prompt (融合后的 Profile)
            user_prompt: User Prompt (问题 + 分析框架)
            timeout_ms: 超时时间（毫秒）

        Returns:
            tuple[Recommendation | None, list[str], float, dict | None]:
                (推荐结果, 错误列表, 耗时秒数, token用量{"input_tokens": int, "output_tokens": int} 或 None)
        """
        from src.domain.models.fusion_result import Recommendation

        step_start = time.time()
        errors: list[str] = []
        recommendation = None

        logger.info("[FusionChat] ========== FusionChat LLM 推理开始 ==========")

        # 计算总 prompt 大小
        total_prompt_size = len(system_prompt) + len(user_prompt)
        estimated_tokens = total_prompt_size // 3  # 粗略估计
        logger.info("[FusionChat] total_prompt_size=%d chars (%.1f KB), 估算 tokens=~%d",
                   total_prompt_size, total_prompt_size / 1024, estimated_tokens)

        # 获取模型选择链（日志在方法内部打印）
        fallback_models, fallback_display = self._get_fallback_models()

        # 重试配置
        max_total_retries = 3          # 总共最多重试 3 次
        retry_base_delay = 1.0         # 基础延迟 1 秒
        retry_delay_multiplier = 2.0   # 延迟倍数

        # 兜底模型（模型链最后一个）
        fallback_model = fallback_models[-1] if fallback_models else "default-fast"
        fallback_model_display = fallback_display[-1] if fallback_display else "fast_model(default)"

        # 总重试循环
        last_error: Exception | None = None
        attempt = 0

        while attempt < max_total_retries:
            # 选择模型：先使用模型链中的模型，用完后用兜底模型
            if attempt < len(fallback_models):
                # 遍历模型阶段
                physical_model = fallback_models[attempt]
                model_display = fallback_display[attempt]
                phase = "模型链"
            else:
                # 超过模型链长度，使用兜底模型持续重试
                physical_model = fallback_model
                model_display = fallback_model_display
                phase = "兜底重试"

            try:
                logger.info(
                    "[FusionChat] [%s] 使用 %s (attempt %d/%d)",
                    phase, model_display, attempt + 1, max_total_retries
                )

                llm_response = self._call_single(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    timeout_ms=timeout_ms,
                    physical_model=physical_model,
                )

                model_elapsed = time.time() - step_start
                logger.info(
                    "[FusionChat] %s 成功, latency=%dms, 总耗时=%.3fs",
                    model_display, llm_response.latency_ms, model_elapsed
                )

                # 提取 token 用量
                token_usage: dict[str, int] | None = None
                if llm_response.usage:
                    token_usage = {
                        "input_tokens": llm_response.usage.input_tokens,
                        "output_tokens": llm_response.usage.output_tokens,
                    }
                    logger.info("[FusionChat] token 用量: input=%d, output=%d",
                               token_usage["input_tokens"], token_usage["output_tokens"])

                # 构建 Recommendation
                if llm_response.raw_text:
                    raw_text_size = len(llm_response.raw_text)
                    logger.info("[FusionChat] raw_text 大小=%d chars (%.1f KB)",
                               raw_text_size, raw_text_size / 1024)

                    recommendation = Recommendation(
                        summary=llm_response.raw_text,
                        decision="yes",
                        risks=[],
                        next_actions=[],
                    )
                    logger.info("[FusionChat] Recommendation 已构建, summary 长度=%d",
                               len(llm_response.raw_text))

                elapsed = time.time() - step_start
                return recommendation, errors, elapsed, token_usage

            except Exception as e:
                last_error = e
                error_msg = str(e)[:100]
                logger.warning(
                    "[FusionChat] [%s] %s 失败 (attempt %d/%d): %s",
                    phase, model_display, attempt + 1, max_total_retries, error_msg
                )
                errors.append(f"[{phase}] {model_display}: {error_msg}")

                # 如果还有重试机会，指数退避
                attempt += 1
                if attempt < max_total_retries:
                    delay = retry_base_delay * (retry_delay_multiplier ** (attempt - 1))
                    logger.info("[FusionChat] 等待 %.1fs 后重试...", delay)
                    time.sleep(delay)

        # 所有重试都失败
        elapsed = time.time() - step_start
        error_summary = f"LLM 调用已重试 {max_total_retries} 次均失败，请联系系统管理员解决问题"
        logger.error("[FusionChat] 调用失败: %s, 总耗时=%.3fs", error_summary, elapsed)
        errors.append(error_summary)
        return None, errors, elapsed, None

    def _get_fallback_models(self) -> tuple[list[str], list[str]]:
        """
        获取 G9 模式的模型选择链

        选择顺序: REASONING_MODEL → BALANCED_MODEL → FAST_MODEL

        Returns:
            tuple[list[str], list[str]]: (物理模型名称列表, 逻辑模型显示名称列表)
        """
        # 从环境变量读取模型名称
        reasoning_model = os.environ.get("LLM_REASONING_MODEL", "").strip()
        balanced_model = os.environ.get("LLM_BALANCED_MODEL", "").strip()
        fast_model = os.environ.get("LLM_FAST_MODEL", "").strip()

        # 构建模型链（过滤空值和重复）
        fallback_chain = []
        fallback_display = []  # 用于日志显示

        if reasoning_model:
            fallback_chain.append(reasoning_model)
            fallback_display.append(f"reasoning_model({reasoning_model})")
        if balanced_model and balanced_model not in fallback_chain:
            fallback_chain.append(balanced_model)
            fallback_display.append(f"balanced_model({balanced_model})")
        elif balanced_model and balanced_model in fallback_chain:
            # 物理模型重复，记录逻辑角色映射，显示实际模型名
            fallback_display.append(f"balanced_model({balanced_model})")
        if fast_model and fast_model not in fallback_chain:
            fallback_chain.append(fast_model)
            fallback_display.append(f"fast_model({fast_model})")
        elif fast_model and fast_model in fallback_chain:
            fallback_display.append(f"fast_model({fast_model})")

        # 如果没有配置任何模型，使用默认值
        if not fallback_chain:
            logger.warning("[FusionChat] 未配置 LLM 模型，使用默认模型链")
            fallback_chain = ["default-reasoning", "default-balanced", "default-fast"]
            fallback_display = ["reasoning_model(default)", "balanced_model(default)", "fast_model(default)"]

        # 打印模型链
        fallback_chain_str = " → ".join(fallback_display)
        logger.info("[FusionChat] 模型选择链: %s", fallback_chain_str)

        return fallback_chain, fallback_display

    def _call_single(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout_ms: int,
        physical_model: str,
    ) -> "LLMResponse":
        """
        调用 LLM Gateway 单次请求

        Args:
            system_prompt: System Prompt
            user_prompt: User Prompt
            timeout_ms: 超时时间（毫秒）
            physical_model: 物理模型名称

        Returns:
            LLMResponse: LLM 响应

        Raises:
            Exception: LLM 调用失败
        """
        from src.domain.models.llm_request import LLMRequest
        from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType, Complexity, CostSensitivity

        task_spec = LLMTaskSpec(
            task_type=TaskType.PROFILE_FUSION,
            complexity=Complexity.MEDIUM,
            need_structured_output=False,
            cost_sensitivity=CostSensitivity.MEDIUM,
            latency_budget_ms=timeout_ms // 2 if timeout_ms > 1000 else 30000,
        )

        llm_request = LLMRequest(
            task_spec=task_spec,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.7,
            max_tokens=4096,
        )

        logger.debug("[FusionChat] 调用 LLM Gateway, model=%s, max_tokens=4096",
                    physical_model)

        # 直接调用 provider，绕过 router 以使用指定模型
        llm_response = self._llm_gateway.provider.generate(llm_request, model=physical_model)

        return llm_response

    # =========================================================================
    # 结果构建（原 GroupFusionService._build_g9_*）
    # =========================================================================

    def build_success_result(
        self,
        group_id: str,
        request: "FusionRequest",
        fused_profile: "FusedProfile",
        recommendation: "Recommendation | None",
        fusion_result: "ProfileFusionResult",
        conv_summary: "GroupConversationSummary",
        warnings: list[str],
        errors: list[str],
        started_at: datetime,
        driver_bot_id: str | None = None,
        step_elapsed: dict[str, float] | None = None,
        token_usage: dict[str, int] | None = None,
    ) -> "FusionResult":
        """
        构建 G9 模式的成功 FusionResult

        Args:
            group_id: Group ID
            request: 融合请求
            fused_profile: 融合后的 Profile
            recommendation: LLM 推荐结果
            fusion_result: Profile 融合结果
            conv_summary: 会话总结结果
            warnings: 警告列表
            errors: 错误列表
            started_at: 开始时间
            driver_bot_id: 发起人 Bot ID
            step_elapsed: 各步骤耗时，包含：
                - step1: Step1 总耗时（并发执行）
                - step2: Step2 Prompt 构建耗时
                - step3: Step3 LLM 调用耗时
                - profile_fusion: Profile 融合耗时
                - group_conversation: 会话总结耗时
                - llm_generation: 最终 LLM 生成耗时

        Returns:
            FusionResult: 融合结果
        """
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        finished_at = datetime.now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)

        # 确保 step_elapsed 不为 None
        step_elapsed = step_elapsed or {}

        # 计算各步骤耗时占比
        step1_ms = int(step_elapsed.get("step1", 0.0) * 1000)
        step2_ms = int(step_elapsed.get("step2", 0.0) * 1000)
        step3_ms = int(step_elapsed.get("step3", 0.0) * 1000)

        # 使用 fusion_result 中的 fusion_id（由上层统一生成）
        fusion_id = fusion_result.fusion_id if fusion_result else ""

        # 日志：记录 driver_bot_id
        logger.info("[FusionChat] driver_bot_id=%s, fusion_id=%s", driver_bot_id, fusion_id)

        logger.info("[FusionChat] ========== 融合完成 ==========")
        logger.info("[FusionChat] 总耗时: %dms | Step1(并发)=%dms | Step2(Prompt构建)=%dms | Step3(LLM回答)=%dms",
                   duration_ms, step1_ms, step2_ms, step3_ms)
        logger.info("[FusionChat] cache_hit=%s, partial_success=%s, errors=%d",
                   fusion_result.cache_hit,
                   fusion_result.fused_profile.has_content() and len(errors) == 0,
                   len(errors))
        logger.info("[FusionChat] 会话总结: success=%s, rewritten=%s, context_count=%d",
                   conv_summary.success,
                   conv_summary.rewritten_question != conv_summary.original_question,
                   conv_summary.context_messages_count)
        if conv_summary.success:
            if conv_summary.rewritten_question:
                rewritten_preview = conv_summary.rewritten_question[:100] + "..." if len(conv_summary.rewritten_question) > 100 else conv_summary.rewritten_question
                logger.info("[FusionChat] 改写问题: %s", rewritten_preview)
            if conv_summary.context_summary:
                summary_preview = conv_summary.context_summary[:100] + "..." if len(conv_summary.context_summary) > 100 else conv_summary.context_summary
                logger.info("[FusionChat] 会话摘要: %s", summary_preview)

        timing = FusionTiming(
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )

        # 构建 fused_profile 字典，添加 driver_bot_id
        fused_profile_dict = fused_profile.model_dump() if fused_profile else None
        if fused_profile_dict is not None:
            fused_profile_dict["driver_bot_id"] = driver_bot_id

        # 记录对话轮次到存储
        if self._storage_service and fusion_id and recommendation:
            try:
                answer_preview = recommendation.summary[:200] if recommendation.summary else ""
                logger.info("[FusionChat] 记录对话轮次: fusion_id=%s, question_len=%d, answer_len=%d",
                           fusion_id, len(request.question), len(recommendation.summary or ""))
                self.record_conversation_turn(
                    fusion_id=fusion_id,
                    question=request.question,
                    answer=recommendation.summary or "",
                    sender_id=driver_bot_id,
                    sender_name=None,
                    response_ms=step3_ms,
                    question_token=token_usage.get("input_tokens") if token_usage else None,
                    response_token=token_usage.get("output_tokens") if token_usage else None,
                    original_question=conv_summary.original_question if conv_summary.success else None,
                    rewritten_question=conv_summary.rewritten_question if conv_summary.success else None,
                    context_summary=conv_summary.context_summary if conv_summary.success else None,
                )
            except Exception as e:
                logger.warning("[FusionChat] 记录对话轮次失败: %s", str(e))

        return FusionResult(
            group_id=group_id,
            fusion_id=fusion_id,
            question=request.question,
            driver_bot_id=driver_bot_id,
            perspectives=[],
            partial_success=fusion_result.fused_profile.has_content() and len(errors) == 0,
            warnings=warnings,
            errors=errors,
            timing=timing,
            fusion_mode="bot_profile_fuse",
            recommendation=recommendation,
            # G9 综合处理信息
            extend_result={
                "fused_profile": fused_profile_dict,
                "group_conversation": {
                    "original_question": conv_summary.original_question if conv_summary.success else None,
                    "rewritten_question": conv_summary.rewritten_question if conv_summary.success else None,
                    "context_summary": conv_summary.context_summary if conv_summary.success else None,
                    "rewrite_success": conv_summary.success,
                    "context_messages_count": conv_summary.context_messages_count,
                } if conv_summary.success or conv_summary.context_messages_count > 0 else None,
                # 细粒度耗时统计（单位：秒，精度：小数点后两位）
                "timing": {
                    "profile_fusion": round(step_elapsed.get("profile_fusion", 0.0), 2),
                    "group_conversation": round(step_elapsed.get("group_conversation", 0.0), 2),
                    "llm_generation": round(step_elapsed.get("llm_generation", 0.0), 2),
                },
            },
        )

    def build_error_result(
        self,
        group_id: str,
        request: "FusionRequest",
        warnings: list[str],
        errors: list[str],
        started_at: datetime,
        fusion_id: str,
        driver_bot_id: Optional[str] = None,
    ) -> "FusionResult":
        """
        构建 G9 模式的错误 FusionResult

        Args:
            group_id: Group ID
            request: 融合请求
            warnings: 警告列表
            errors: 错误列表
            started_at: 开始时间
            fusion_id: 融合ID（由上层统一生成）
            driver_bot_id: 发起人 Bot ID

        Returns:
            FusionResult: 错误结果
        """
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        finished_at = datetime.now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)

        # 更新存储状态为失败
        if self._storage_service and fusion_id:
            try:
                error_msg = "; ".join(errors[:3]) if errors else "Unknown error"
                self._storage_service.update_status(
                    fusion_id=fusion_id,
                    status=FusionStatus.FAILED,
                    message=error_msg,
                )
            except Exception as e:
                logger.warning("[FusionChat] 更新状态失败: %s", str(e))

        timing = FusionTiming(
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )
        return FusionResult(
            group_id=group_id,
            fusion_id=fusion_id,
            question=request.question,
            driver_bot_id=request.driver_bot_id,
            perspectives=[],
            partial_success=False,
            warnings=warnings,
            errors=errors,
            timing=timing,
            fusion_mode="bot_profile_fuse",
        )


__all__ = [
    "FusionExpertChatService",
]