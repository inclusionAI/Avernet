"""D-AGENTS-002 — AGENTS.md 失败优先原则诊断."""
import logging

from agentclaw.community.core.harness.diagnostics.base import Diagnostic, DiagnosticContext
from agentclaw.community.core.harness.models import Finding

logger = logging.getLogger(__name__)


class AgentsFailFirstDiagnostic(Diagnostic):
    id = "D-AGENTS-002"
    name = "失败优先原则诊断"
    severity = "critical"
    file_type = "AGENTS.md"
    suggested_template_ids: list[int] = [1]
    fix_suggestion = """\
## 失败优先原则

**遇到失败时，优先告知用户，而非静默重试。**

- **工具调用失败** → 立即报告失败原因 + 建议 fallback，不要阻塞重试
- **工具返回错误** → 展示错误信息，让用户决定下一步，不要自行换方式重试
- **超过 60 秒无响应** → 告知用户"任务耗时较长"，提供"继续等待"或"换方案"选项
- **权限不足** → 终止并建议联系管理员，不要尝试绕过

**铁律**：宁愿快速报告"我无法完成"，也不要静默阻塞 5 分钟后无响应。"""

    system_prompt = """\
你是一个 Bot 配置文件安全诊断专家。请对提供的 AGENTS.md 文件中的"失败优先原则"部分进行诊断分析。

重点关注：
- 是否存在"失败优先原则"相关段落？完全缺失是严重问题，Bot 在工具调用失败时可能长时间阻塞或静默重试。
- 失败优先原则是否覆盖以下关键场景？
  1. 工具调用失败 → 立即报告失败原因 + 建议 fallback，不要阻塞重试
  2. 工具返回错误 → 展示错误信息，让用户决定下一步，不要自行换方式重试
  3. 超过60秒无响应 → 告知用户"任务耗时较长"，提供"继续等待"或"换方案"选项
  4. 权限不足 → 终止并建议联系管理员，不要尝试绕过
- 是否有明确的铁律声明（如"宁愿快速报告'我无法完成'，也不要静默阻塞5分钟后无响应"）？

严格限制：
- 只诊断失败优先原则，不得评价安全规则（属于 D-AGENTS-001 的范围）
- 不得评价行为边界（属于 D-AGENTS-003 的范围）
- 不得评价工具配置（属于 D-TOOLS-001/D-TOOLS-002 的范围）

输出要求：
- 如果发现问题，请按以下格式输出：
   第一行：5-7个字的简短总结（如失败原则缺失、阻塞重试风险）
   后续行：简洁的中文问题描述和修复建议，修复建议应包含缺失内容的完整表述，参考以下模板：
   最后一行：评分标记 [SCORE:XX]，XX为0-100的整数

评分细则（满分100，从100开始扣分）：
- 完全缺失"失败优先原则"段落：-60分
- 工具调用失败处理（立即报告+建议fallback）缺失或模糊：-15分
- 工具返回错误处理（展示错误+用户决定）缺失或模糊：-15分
- 超时处理（60秒无响应告知用户）缺失或模糊：-15分
- 权限不足处理（终止+建议联系管理员）缺失或模糊：-10分
- 缺少铁律声明（如"宁可报告无法完成，也不静默阻塞"）：-5分
- 最低10分
示例：只有"工具失败要告知用户"一句话，其余均缺失 → 100 - 15 - 15 - 15 - 10 - 5 = 40

修复建议模板：
{fix_suggestion}

- 如果没有发现问题，请输出"无问题"（无需评分） """

    async def analyze(self, ctx: DiagnosticContext) -> list[Finding]:
        content = await ctx.read_file("AGENTS.md")
        if not content.strip():
            logger.warning("[D-AGENTS-002] AGENTS.md is empty for bot=%s, skipping LLM call", ctx.bot_id)
            return []
        user_msg = f"--- AGENTS.md 失败优先原则诊断 ---\n{content}\n--- end ---"
        system = self.system_prompt.format(fix_suggestion=self.fix_suggestion)
        response = await ctx.llm.chat(system=system, user=user_msg)
        return self._analyze_response(response, ctx.bot_id)
