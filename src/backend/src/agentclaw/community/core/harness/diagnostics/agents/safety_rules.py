"""D-AGENTS-001 — AGENTS.md Safety Rules 诊断."""
import logging

from agentclaw.community.core.harness.diagnostics.base import Diagnostic, DiagnosticContext
from agentclaw.community.core.harness.models import Finding

logger = logging.getLogger(__name__)


class AgentsSafetyRulesDiagnostic(Diagnostic):
    id = "D-AGENTS-001"
    name = "Safety Rules 诊断"
    severity = "critical"
    file_type = "AGENTS.md"
    suggested_template_ids: list[int] = [1]
    fix_suggestion = """\
## Safety Rules

- **SR01** 不安全不做——涉及数据删除/修改/外发，先确认再执行
- **SR02** 禁止自主创建文件——工具缺失时报告用户，不要自行创建替代文件或配置
- **SR03** 禁止阻塞等待——工具调用失败后立即告知用户，不要静默重试超过 2 次，不要阻塞等待超过 60 秒
- **SR04** 禁止猜测修改配置——遇到异常时暂停并报告，不要自行猜测修复方案
- **SR05** 禁止批量操作无上限——涉及批量处理时必须先确认范围
- **SR06** 先查再调——调用陌生工具前，先确认 server 和 tool 名称，禁止凭猜测构造路径
- **SR07** 修正优先——工具调用失败后，先分析错误原因、修正参数重试同一 tool，仅在确认当前 tool 确实不适用时才换 tool；禁止"不分析错误就换 tool"的碰运气行为
- **SR08** 禁止空参数——向工具传参数时禁止传空对象 `{{}}`，每个必填参数必须提供有效值；不确定参数格式时先查文档或参数模板
- **SR09** 连续失败熔断——同一 tool 连续失败 2 次后停止重试，向用户报告失败原因和建议，不要继续尝试
- **SR10** 异常服务告知——Skill 或工具调用连续返回异常时，不要静默重试，应主动告知用户当前服务可能不可用，建议确认服务状态或联系管理员"""

    system_prompt = """\
你是一个 Bot 配置文件安全诊断专家。请对提供的 AGENTS.md 文件中的 Safety Rules 部分进行诊断分析。

重点关注：
- 是否存在 Safety Rules 相关段落？完全缺失是严重问题，Bot 将缺乏安全防护约束。
- Safety Rules 是否覆盖关键安全场景？以下是应覆盖的核心规则（缺失任何一条都应指出）：
  1. SR01 不安全不做——涉及数据删除/修改/外发，先确认再执行
  2. SR02 禁止自主创建文件——工具缺失时报告用户，不要自行创建替代文件或配置
  3. SR03 禁止阻塞等待——工具调用失败后立即告知用户，不要静默重试超过2次，不要阻塞等待超过60秒
  4. SR04 禁止猜测修改配置——遇到异常时暂停并报告，不要自行猜测修复方案
  5. SR05 禁止批量操作无上限——涉及批量处理时必须先确认范围
  6. SR06 先查再调——调用陌生工具前，先确认server和tool名称，禁止凭猜测构造路径
  7. SR07 修正优先——工具调用失败后，先分析错误原因、修正参数重试同一tool，仅在确认当前tool确实不适用时才换tool
  8. SR08 禁止空参数——向工具传参数时禁止传空对象{{}}，每个必填参数必须提供有效值
  9. SR09 连续失败熔断——同一tool连续失败2次后停止重试，向用户报告失败原因和建议
  10. SR10 异常服务告知——Skill或工具调用连续返回异常时，应主动告知用户当前服务可能不可用
- 已有的 Safety Rules 描述是否明确、可执行？模糊的约束（如"注意安全"）几乎无效。

严格限制：
- 只诊断 Safety Rules 部分，不得评价行为边界（属于 D-AGENTS-003 的范围）
- 不得评价失败优先原则（属于 D-AGENTS-002 的范围）
- 不得评价工具配置（属于 D-TOOLS-001/D-TOOLS-002 的范围）

输出要求：
- 如果发现问题，请按以下格式输出：
   第一行：5-7个字的简短总结（如安全规则缺失、安全约束模糊）
   后续行：简洁的中文问题描述和修复建议，修复建议应包含缺失规则的完整内容，参考以下模板：
   最后一行：评分标记 [SCORE:XX]，XX为0-100的整数

评分细则（满分100，从100开始扣分）：
- 完全缺失 Safety Rules 段落：-50分
- SR01~SR10 每缺失一条核心规则：-8分
- SR01~SR10 每条存在但描述模糊或不可执行（如仅写"注意安全"）：-4分
- 规则间存在矛盾：-10分
- 最低10分
示例：有 SR01~SR05 完整但 SR06~SR10 缺失 → 100 - 5×8 = 60

修复建议模板：
{fix_suggestion}

- 如果没有发现问题，请输出"无问题"（无需评分） """

    async def analyze(self, ctx: DiagnosticContext) -> list[Finding]:
        content = await ctx.read_file("AGENTS.md")
        if not content.strip():
            logger.warning("[D-AGENTS-001] AGENTS.md is empty for bot=%s, skipping LLM call", ctx.bot_id)
            return []
        user_msg = f"--- AGENTS.md Safety Rules 诊断 ---\n{content}\n--- end ---"
        system = self.system_prompt.format(fix_suggestion=self.fix_suggestion)
        response = await ctx.llm.chat(system=system, user=user_msg)
        return self._analyze_response(response, ctx.bot_id)
