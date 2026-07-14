"""VerifyPromptComposer — LLM 生成每域多维度领域验证 prompt。"""

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
from src.domain.models.verify_dto import CapabilityProbes, DimensionProbe, VerifyData

if TYPE_CHECKING:
    from src.application.services.llm_gateway_service import LLMGatewayService

logger = logging.getLogger(__name__)


class _ProbeOutput(BaseModel):
    """LLM 输出的结构化验证 prompt。"""

    capability_probes: list[CapabilityProbes]


INTRO_PROMPT = "请你自我介绍一下：你的角色定位是什么？你具有哪些核心能力和专业技能？你能执行什么样的工作任务？请尽可能详细地描述。"

PROBE_TEMPLATE = """你是一个 bot 能力审计面试官。你正在对一个待注册 bot 做“声明能力可信度面试”。

## 第一轮：Bot 自我介绍
{bot_intro}

## Bot 注册信息
- 核心身份: {soul_md}
- 声明的能力: {capabilities_desc}
- 技能: {skills_desc}

## 你的任务与核心验收标准
你需要根据 bot 的【自我介绍】和【注册声明】，为它生成第二轮“审计式追问”。

你的目标不是出一套通用技术考试题，也不是逼问底层源码实现细节。
我们的核心验收标准是：**如果 bot 真的具备某项非默认能力，它必然能够准确写出对应专属工具/Skill 的调用 JSON Schema，并能清晰说明数据在工具之间的真实流转过程。**
你需要通过追问，逼迫 bot 展露出这些“硬性的结构化执行证据”，以此判断它是否只是一个通用闲聊模型在扮演专家。

## 🚨 绝对禁止的行为（重要红线）
1. **禁止捏造未声明参数/陷阱题**：绝不能凭空捏造 bot 没说过的参数名、未声明的错误码或不存在的业务场景。必须 100% 针对它已声明的内容提问！
2. **禁止形而上的概念拷问**：绝不能问“你的判断逻辑是什么”“你如何权衡优先级”。（这种问题强模型可以靠常识背诵，无法查验真实能力）。
3. **禁止让 bot 设计开放式方案**：绝不能问“你会如何设计这个诊断报告”。

## 生成问题前，你必须先在脑中完成以下分析
1. 从 bot 自我介绍中提取明确的**技术锚点**：
   - 非默认 skill 名称
   - 专属 API / 工具 / MCP 接口
   - 明确声明过的输入参数、业务对象
2. 识别哪些能力是 bot 明确声明过的，哪些只是泛化描述。
3. 你的问题必须直接锚定上述提取出的“技术锚点”。

## 高质量问题的优先方向 (核心提问策略)
每个能力域请严格优先使用以下三种提问方式之一：

1. **Schema 默写 (schema_demonstration)**：
   - **目的**：验证 bot 是否真的挂载了该工具，而不是泛泛而谈。
   - **问法示例**：“你声明使用 `[某Skill/工具]` 来处理 `[某场景]`。当用户说 `[具体的一句人话请求]` 时，请写出你构造给该工具的完整 JSON Payload（包含你认为必填的参数名和结构）。”

2. **工作流数据流转 (workflow_data_flow)**：
   - **目的**：验证多步操作的真实性，防止背诵空泛的流程。
   - **问法示例**：“你提到工作流是 `[步骤A]` 接着 `[步骤B]`。请问在调用完 `[工具A]` 后，你具体会提取返回结果中的哪个/哪些关键信息，作为入参传递给接下来的 `[工具B]`？”

3. **已声明的异常拦截 (declared_exception_handling)**：
   - **目的**：验证边界控制的真实性（仅限它自己提到过的异常）。
   - **问法示例**：“你提到如果缺少 `[某前置条件]` 或遇到 `[某明确声明的错误]` 会执行降级/停止。请问在这种情况下，你输出给用户的 JSON 结构或固定反馈模板具体是什么样的？”

## 默认能力限制
以下为平台默认能力，所有 bot 都具备，不构成差异化价值：
- OpenClaw 平台内置工具与系统基础工具，如 read / write / edit / exec / browser / search / web_fetch / message / cron / sessions / subagents / memory 等
- 平台默认 skill 或官方标准 skill，如 bcs-coordination、bot-soul-manage、web-search-asap、teamclaw-cli 等
- 默认模型、上下文窗口、插件、频道、节点、网关、可观测、安全守护等平台基础设施能力
- “可管理 MCP / 可添加 MCP server / 支持 stdio 或 HTTP” 这类平台扩展能力本身

要求：绝对不要针对默认能力本身生成追问。

## 输出要求
1. 根据 bot 的声明能力，生成 1-3 个能力域。
2. 每个能力域下生成 2-3 个追问。
3. 每个能力域**至少有 1 个问题必须显式要求 bot 写出 JSON Payload (入参 Schema)**。
4. dimension 用简短英文小写下划线命名（如 `schema_demonstration`, `workflow_data_flow`, `exception_handling`）。
5. probe_prompt 用中文，问题必须像“系统集成联调”一样具体、务实，带有直接要求出具结构化数据的指令。

## 输出前自检
请在输出前自检：
- 我是否捏造了 bot 没说过的参数或陷阱？（如果有，立刻重写！）
- 问题是否强关联于 bot 自我介绍中提及的具体工具名？

请严格按以下 JSON 格式输出，不要输出任何其他内容：
{{
  "capability_probes": [
    {{
      "capability_name": "能力域名称",
      "dimensions": [
        {{"dimension": "schema_demonstration", "probe_prompt": "问题1"}},
        {{"dimension": "workflow_data_flow", "probe_prompt": "问题2"}},
        {{"dimension": "declared_exception_handling", "probe_prompt": "问题3"}}
      ]
    }}
  ]
}}"""


class VerifyPromptComposer:
    """LLM 生成面试式验证 prompt：基于 bot 自我介绍生成深度追问。"""

    def __init__(
        self,
        llm_gateway: LLMGatewayService,
        latency_budget_ms: int = 30000,
        max_tokens: int = 4096,
    ) -> None:
        self._llm = llm_gateway
        self._latency_budget_ms = latency_budget_ms
        self._max_tokens = max_tokens

    async def compose(self, verify_data: VerifyData) -> list[CapabilityProbes]:
        user_prompt = self._build_prompt(verify_data)
        logger.info(
            "[VerifyPromptComposer] 生成追问 prompt: capabilities_count=%d "
            "soul_md_len=%d skill_sets_count=%d bot_intro_len=%d",
            len(verify_data.capabilities),
            len(verify_data.soul_md),
            len(verify_data.skill_sets),
            len(verify_data.bot_intro),
        )
        request = LLMRequest(
            task_spec=LLMTaskSpec(
                task_type=TaskType.EXTRACTION,
                complexity=Complexity.MEDIUM,
                need_structured_output=True,
                cost_sensitivity=CostSensitivity.MEDIUM,
                latency_budget_ms=self._latency_budget_ms,
            ),
            system_prompt="你是一个能力验证面试官。请根据 bot 的自我介绍生成深度追问问题，严格按照要求的 JSON 格式输出。",
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=self._max_tokens,
        )
        try:
            response = await asyncio.to_thread(self._llm.generate, request)
            raw_text = response.raw_text if response.raw_text else ""
            return self._parse_output(raw_text)
        except Exception:
            logger.exception("VerifyPromptComposer: LLM 生成失败")
            raise

    @staticmethod
    def get_intro_prompt() -> str:
        """返回第一轮自我介绍的提问 prompt。"""
        return INTRO_PROMPT

    def _build_prompt(self, data: VerifyData) -> str:
        caps_desc = "\n".join(
            f"  - {c.name} (级别: {c.level.value})" for c in data.capabilities
        )
        skills_desc = "\n".join(
            self._format_skill(s) for s in data.skill_sets
        ) or "  (无)"
        soul_md = data.soul_md or "(无)"
        bot_intro = data.bot_intro or "（bot 未提供自我介绍）"

        return PROBE_TEMPLATE.format(
            bot_intro=bot_intro,
            soul_md=soul_md,
            capabilities_desc=caps_desc,
            skills_desc=skills_desc,
        )

    @staticmethod
    def _format_skill(s: dict) -> str:
        name = s.get("name", "unknown")
        desc = s.get("description", "")
        content = s.get("content", "")
        parts = [f"  - {name}"]
        if desc:
            parts.append(f"    描述: {desc}")
        if content:
            parts.append(f"    内容: {content}")
        return "\n".join(parts)

    def _parse_output(self, raw: str) -> list[CapabilityProbes]:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1])
        parsed = json.loads(cleaned)
        validated = _ProbeOutput.model_validate(parsed)
        return validated.capability_probes