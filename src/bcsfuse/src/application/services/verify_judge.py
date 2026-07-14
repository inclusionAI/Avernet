"""VerifyJudge — LLM 评判每维度 0-1 置信度。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_task_spec import (
    Complexity,
    CostSensitivity,
    LLMTaskSpec,
    TaskType,
)
from src.domain.models.verify_dto import DimensionJudgment, DimensionResult, VerifyData

if TYPE_CHECKING:
    from src.application.services.llm_gateway_service import LLMGatewayService

logger = logging.getLogger(__name__)


class _JudgeOutput(BaseModel):
    """单个维度的 LLM 评判输出。"""

    dimension: str
    confidence: float
    reasoning: str = ""


JUDGE_PROMPT_TEMPLATE = """你是一个 bot 能力审计评判员。请评估下面这条回复，是否为 bot 具备该能力提供了"可信证据"。

## 能力背景
声明的能力域: {capability_name} (级别: {capability_level})
验证维度: {dimension}

## 验证问题
{probe_prompt}

## Bot 回复
{response}

## 你的评判目标
你不是在判断"这段话是否流畅"或"是否像专家文章"，而是在判断：
这条回复是否足以证明 bot 真实具备该项能力，而不是仅仅具备通用知识表达能力、角色扮演能力或包装能力。

你需要特别区分以下三类情况：
1. **通用知识型回答**：任何强模型都可能给出的常识性、模板化、教科书式回答
2. **专属工作流型回答**：包含非默认 skill、专属 API、业务对象、输入参数、条件分支、错误处理、平台内部环境锚点等，体现特定领域工作流
3. **历史执行型回答**：有 memory、历史案例、任务记录、执行痕迹、结果反馈等支撑

## 🚨 评判红线与【重要豁免特权】（必读）

### 1. 默认工具包装判定与【豁免条件】
- **红线**：把 read、write、exec、search、browser 等平台默认能力简单包装成专业能力，但没有任何特定业务逻辑。
- **✅ 豁免与加分条件**：如果 Bot 在使用默认工具（如 `bash`, `browser`）时，**明确结合了专属业务资产**（例如：使用 `bash` 运行了特定的内部物理路径脚本如 `~/.openclaw/.../xxx.py`，或使用 `browser` 访问了特定的内部私有域名如 `https://internal-asset.example.com/...`）。这表明 Bot 在使用通用工具执行私有环境任务，属于**极强的专属能力证据**，绝对不可因此判定为"默认工具包装"而扣分！

### 2. Schema 形态与【预设纠正特权】
- **红线**：答非所问，回避提供具体的技术结构。
- **✅ 预设纠正与反驳加分（极其重要）**：如果面试官预设了某种调用形态（例如要求"写出 JSON Payload"），但被测 Bot 明确指出了该预设与实际情况不符（例如："该技能不是通过 JSON API 调用，而是通过脚本/浏览器自动化完成"），并给出了具体的底层实现细节（如具体脚本绝对路径、内网操作 URL）。这种"反驳"是 Bot 深度掌握自身物理执行环境的**极强证据**！必须给予高分（>=0.80），**绝对不可**因为"没有提供题目要求的 JSON"而降分。

### 3. 常见扣分红线（若触犯且无豁免，应显著降分）
- **知识泛化**：回复只有教科书式流程描述，没有任何技术锚点、业务锚点或专属工具链。
- **空泛角色扮演**：大量"我会帮助你贴心处理"之类拟人化表述，缺少逻辑分支与执行证据。
- **伪专业锚点**：表面提到一些名词，但无法形成完整、合理、可执行的工作流。

## 证据通道
请综合以下三类证据判断，不要只依据单一因素：

### A. 历史执行证据（最强）
- memory 中的历史案例 / 平台观察结果 / 任务痕迹

### B. 专属能力证据（强）
- 明确的非默认 skill / 专属工具 / API / 内部操作脚本路径
- 明确的参数名、输入 schema（或带具体 Flag/路径的 CLI 命令）
- 明确的内网业务域名、平台私有标识
- 错误处理逻辑、条件分支、特定环境工作流

### C. 工作流与边界证据（中）
- 能做 / 不能做的限制条件、风险控制、转交边界

## 评分区间说明（严格执行）

### 0.85 - 1.00：强证据（历史执行级 / 强硬核验证级）
满足以下任一类：
1. 提供了清晰的历史执行证据（memory 等），且与回答一致；
2. **完美反驳与纠正**：准确纠正了面试官题目中的错误预设（如否定了 JSON API 调用方式），并给出了极度详尽、真实的内部底层路径（如私有脚本绝对路径、内网业务 URL），展现了对真实环境的深度掌握；
3. 准确默写了专属工具的 JSON Payload/Schema、具体的提取字段，且数据流转逻辑高度契合特定业务系统特征，通用大模型无法凭空猜出。

这一区间意味着：不仅会描述，而且给出了无法伪造的环境/结构级证据，高度可信。

### 0.60 - 0.84：中强证据（专属工作流描述级）
回复具备较强的差异化能力证据：
- 明确提到多个非默认 skill / 工具 / API / 业务对象。
- 给出了基本的输入输出形式或执行步骤，但 Schema/脚本路径 不够详尽。
- 逻辑自洽，表现出特定领域的工作流，而不是泛泛而谈。
如果没有历史记录，但流程描述正确且具备一定工具锚点，通常落入此区间。

### 0.20 - 0.59：弱证据（知识型伪装 / 泛化描述）
回复部分相关，但主要表现为：
- 通用知识堆砌、教科书式流程。
- 空泛边界话术、角色扮演式自述。
- 把通用工具当成专属能力，且没有提供任何专属的内部路径、参数或业务对象。
- 完全无法证明是"这个 bot 真实挂载的能力"。

### 0.00 - 0.19：无证据
回复明显无关、错误、空泛，或产生严重幻觉。

## 你的逻辑分析要求
请在脑中执行以下分析，但最终只输出 JSON：
1. **识别环境/结构锚点**：是否存在真实的 JSON Schema、特定的 CLI 命令、私有代码路径、内网域名？
2. **检查特权触发**：Bot 是否纠正了面试官的预设？是否合理利用了默认工具（bash/browser）去打内网资产？如果触发，立刻拔高评估基准。
3. **识别通用知识成分**：指出回复中哪些内容只是通用训练语料中常见的常识。
4. **分析工作流真实性**：这些锚点是否能组成一个真实可执行的操作步骤。

请严格按以下 JSON 格式输出，不要输出任何其他内容：
{{
  "dimension": "{dimension}",
  "confidence": 0.85,
  "reasoning": "用简短中文说明：它回答了什么证据（历史/工具/结构/纠正预设）。它提供了哪些技术锚点（如具体脚本路径/Schema）。触发了什么特权/红线。最终为什么能/不能证明能力。"
}}"""


def _get_judge_prompt_template() -> str:
    """获取评判 prompt 模板，优先从 DRM 配置读取，回退到本地默认。"""
    from src.application.utils.drm_config_helper import get_judge_prompt_template
    drm_template = get_judge_prompt_template()
    if drm_template:
        return drm_template
    return JUDGE_PROMPT_TEMPLATE


class VerifyJudge:
    """LLM 评判每维度 0-1 置信度。"""

    def __init__(
        self,
        llm_gateway: LLMGatewayService,
        latency_budget_ms: int = 15000,
        max_tokens: int = 1024,
    ) -> None:
        self._llm = llm_gateway
        self._latency_budget_ms = latency_budget_ms
        self._max_tokens = max_tokens

    async def judge(
        self,
        verify_data: VerifyData,
        results: list[DimensionResult],
    ) -> list[DimensionJudgment]:
        judgments: list[DimensionJudgment] = []
        cap_level_map = {c.name: c.level.value for c in verify_data.capabilities}

        for result in results:
            if result.failed:
                judgments.append(
                    DimensionJudgment(
                        capability_name=result.capability_name,
                        dimension=result.dimension,
                        confidence=0.0,
                        reasoning="技术性失败，无法评判",
                    )
                )
                continue

            try:
                judgment = await self._judge_single(result, cap_level_map)
                judgments.append(judgment)
                logger.debug(
                    "[VerifyJudge] 评判结论 %s/%s: confidence=%.2f reasoning=%s",
                    judgment.capability_name,
                    judgment.dimension,
                    judgment.confidence,
                    judgment.reasoning[:200],
                )
            except Exception:
                logger.exception(
                    "VerifyJudge: 评判失败 %s/%s",
                    result.capability_name,
                    result.dimension,
                )
                judgments.append(
                    DimensionJudgment(
                        capability_name=result.capability_name,
                        dimension=result.dimension,
                        confidence=0.0,
                        reasoning="评判异常",
                    )
                )

        logger.info(
            "[VerifyJudge] 全部评判完成: %d 项, 平均置信度=%.2f",
            len(judgments),
            sum(j.confidence for j in judgments) / len(judgments) if judgments else 0.0,
        )
        return judgments

    async def _judge_single(
        self,
        result: DimensionResult,
        cap_level_map: dict[str, str],
    ) -> DimensionJudgment:
        level = cap_level_map.get(result.capability_name, "unknown")
        prompt_template = _get_judge_prompt_template()
        user_prompt = prompt_template.format(
            capability_name=result.capability_name,
            capability_level=level,
            dimension=result.dimension,
            probe_prompt=result.probe_prompt,
            response=result.response_content,
        )
        request = LLMRequest(
            task_spec=LLMTaskSpec(
                task_type=TaskType.RATIONALE_GENERATION,
                complexity=Complexity.LOW,
                need_structured_output=True,
                cost_sensitivity=CostSensitivity.MEDIUM,
                latency_budget_ms=self._latency_budget_ms,
            ),
            system_prompt="你是一个能力验证评判专家。请严格按照要求的 JSON 格式输出。",
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=self._max_tokens,
        )
        response = await asyncio.to_thread(self._llm.generate, request)
        raw_text = response.raw_text if response.raw_text else ""
        return self._parse_output(result.capability_name, raw_text)

    def _parse_output(self, capability_name: str, raw: str) -> DimensionJudgment:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1])
        parsed = json.loads(cleaned)
        validated = _JudgeOutput.model_validate(parsed)
        confidence = max(0.0, min(1.0, validated.confidence))
        return DimensionJudgment(
            capability_name=capability_name,
            dimension=validated.dimension,
            confidence=confidence,
            reasoning=validated.reasoning,
        )