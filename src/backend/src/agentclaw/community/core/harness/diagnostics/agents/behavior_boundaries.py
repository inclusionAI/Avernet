"""D-AGENTS-003 — AGENTS.md 行为边界诊断."""
import logging

from agentclaw.community.core.harness.diagnostics.base import Diagnostic, DiagnosticContext
from agentclaw.community.core.harness.models import Finding

logger = logging.getLogger(__name__)


class AgentsBehaviorBoundariesDiagnostic(Diagnostic):
    id = "D-AGENTS-003"
    name = "行为边界诊断"
    severity = "critical"
    file_type = "AGENTS.md"
    suggested_template_ids: list[int] = [1]
    fix_suggestion = """\
## 行为边界

**Stop before you leap**：
- 涉及创建文件、执行命令、修改状态的操作 → **先说明计划，等用户确认再执行**
- 工具或 Skill 未找到 → 报告错误，**禁止自行创建、安装或配置**
- 请求模糊 → 要求澄清，**不要猜测用户意图**

**Prefer safe defaults**：
- 多种方案时选择最安全的，不是最快的
- 不确定时说"我不知道"，不要编造答案

**Check before you start**：
- 执行任务前，先验证关键前置条件——必要数据依赖是否就绪、外部服务是否可用
- 任一脚本、工具未就绪时，主动告知用户缺少什么、如何准备，而非启动后静默失败"""

    system_prompt = """\
你是一个 Bot 配置文件安全诊断专家。请对提供的 AGENTS.md 文件中的"行为边界"部分进行诊断分析。

重点关注：
- 是否存在"行为边界"相关段落？完全缺失是严重问题，Bot 可能在未获授权时执行危险操作。
- 行为边界是否覆盖以下三个核心维度？
  1. Stop before you leap——涉及创建文件、执行命令、修改状态的操作，先说明计划等用户确认再执行；工具或Skill未找到时报错，禁止自行创建、安装或配置；请求模糊时要求澄清，不要猜测用户意图
  2. Prefer safe defaults——多种方案时选择最安全的而非最快的；不确定时说"我不知道"，不要编造答案
  3. Check before you start——执行任务前先验证关键前置条件（必要数据依赖是否就绪、外部服务是否可用）；任一脚本、工具未就绪时主动告知用户缺少什么、如何准备，而非启动后静默失败
- 已有的行为边界描述是否明确、具体？模糊的约束（如"小心操作"）几乎无效。

严格限制：
- 只诊断行为边界，不得评价安全规则（属于 D-AGENTS-001 的范围）
- 不得评价失败优先原则（属于 D-AGENTS-002 的范围）
- 不得评价工具配置（属于 D-TOOLS-001/D-TOOLS-002 的范围）

输出要求：
- 如果发现问题，请按以下格式输出：
   第一行：5-7个字的简短总结（如行为边界缺失、执行前未确认）
   后续行：简洁的中文问题描述和修复建议，修复建议应包含缺失内容的完整表述，参考以下模板：
   最后一行：评分标记 [SCORE:XX]，XX为0-100的整数

评分细则（满分100，从100开始扣分）：
- 完全缺失"行为边界"段落：-60分
- "Stop before you leap"维度（创建/执行/修改前确认；禁止自行安装配置；请求模糊要求澄清）：
  - 整个维度缺失：-25分
  - 部分子项缺失：每个子项-8分
  - 子项存在但模糊：每个子项-4分
- "Prefer safe defaults"维度（选最安全方案；不确定时承认不知道）：
  - 整个维度缺失：-20分
  - 部分子项缺失：每个子项-8分
  - 子项存在但模糊：每个子项-4分
- "Check before you start"维度（执行前验证前置条件；未就绪时主动告知）：
  - 整个维度缺失：-20分
  - 部分子项缺失：每个子项-8分
  - 子项存在但模糊：每个子项-4分
- 最低10分
示例：有"Stop before you leap"但缺另外两个维度 → 100 - 20 - 20 = 60

修复建议模板：
{fix_suggestion}

- 如果没有发现问题，请输出"无问题"（无需评分） """

    async def analyze(self, ctx: DiagnosticContext) -> list[Finding]:
        content = await ctx.read_file("AGENTS.md")
        if not content.strip():
            logger.warning("[D-AGENTS-003] AGENTS.md is empty for bot=%s, skipping LLM call", ctx.bot_id)
            return []
        user_msg = f"--- AGENTS.md 行为边界诊断 ---\n{content}\n--- end ---"
        system = self.system_prompt.format(fix_suggestion=self.fix_suggestion)
        response = await ctx.llm.chat(system=system, user=user_msg)
        return self._analyze_response(response, ctx.bot_id)
