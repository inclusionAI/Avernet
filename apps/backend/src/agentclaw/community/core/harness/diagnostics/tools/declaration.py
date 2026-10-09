"""D-TOOLS-001 — TOOLS.md 工具调用规范诊断.

检查 TOOLS.md 是否明确了统一的工具调用规范和格式，
对应文档中"一、核心调用纪律与原则"和"二、参数传递格式与翻译规则"。
"""
import logging

from agentclaw.community.core.harness.diagnostics.base import Diagnostic, DiagnosticContext
from agentclaw.community.core.harness.models import Finding
from agentclaw.community.core.harness.services.llm import DIAGNOSTIC_MAX_TOKENS

logger = logging.getLogger(__name__)


class ToolsDeclarationDiagnostic(Diagnostic):
    id = "D-TOOLS-001"
    name = "工具调用原则诊断"
    severity = "critical"
    file_type = "TOOLS.md"
    suggested_template_ids: list[int] = [2]
    fix_suggestion = """\
    ## 核心调用纪律与原则

1. **查阅映射表**：调用前先查阅下文的【场景与工具映射速查】，确认用户意图对应的 MCP 服务器全称。
2. **Selector 必须完整**：`服务器全称.工具名` 必须作为一个整体传递，**绝对不能拆分**。
   * ❌ 错误：`mcporter call server_name tool_name`（会报 Unknown MCP server）
   * ✅ 正确：`mcporter call server_name.tool_name`
3. **获取真实参数定义（禁止猜测）**：如果不知道参数名，**不要凭经验猜测**（如误以为分页是 limit/pageSize）。必须先拉取 Schema：
   * `mcporter list <服务器全称> --schema --json`
   * 严格使用 `inputSchema.properties` 中定义的参数名和类型。
4. **异常重试原则**：调用失败后最多重试 1 次（需调整参数），第 2 次仍失败则向用户如实报告。

---

## 参数传递格式与翻译规则

### 1. 强烈推荐：使用 `--args` JSON 格式（首选）
为避免 Shell 解析错误、异步超时以及数据类型被错误推断（如纯数字字符串被当成 int），**绝大多数场景下请优先使用 `--args` JSON 格式**：

```bash
mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_search \
  --args '{"q": "关键词", "pageSize": 10}'
```
> **JSON 格式优势**：无歧义、类型明确（数字/字符串/布尔值一目了然）、支持复杂/嵌套结构、避免 key=value 触发的异步挂起。

### 2. 备选方案：`key=value` 格式
仅适用于参数极少、无嵌套且没有纯数字字符串的简单调用。若需传递纯数字字符串，必须加 `--raw-strings`：
```bash
# 强制将所有值解析为字符串，防止 ID 被解析为数字
mcporter call ...fund_chain_track --raw-strings node_id="2088342064891162"
```
"""
    system_prompt = """\
你是一个 Bot 工具配置诊断专家。请对提供的 TOOLS.md 文件中的"工具调用规范与格式"部分进行诊断分析。

重点关注：
- 是否存在工具调用规范相关段落？完全缺失是严重问题，Bot 将在调用工具时缺乏纪律约束，容易出错。
- 调用规范是否覆盖以下核心维度？
  1. 调用前查阅映射表——调用工具前先确认 MCP 服务器全称和工具名，不能凭猜测构造
  2. Selector 必须完整——`服务器全称.工具名` 必须作为一个整体传递，绝对不能拆分
  3. 禁止猜测参数——不知道参数名时必须先拉取 Schema（如 mcporter list <server> --schema --json），严格使用 Schema 中定义的参数名和类型
  4. 异常重试原则——调用失败后最多重试 1 次（需调整参数），第 2 次仍失败则向用户如实报告
  5. 参数传递格式——优先使用 --args JSON 格式（避免 Shell 解析错误、类型推断问题）；备选 key=value 格式仅适用于简单场景；纯数字字符串必须加 --raw-strings
  6. 工具签名翻译规则——文档中的函数签名是描述格式，绝对不能直接作为 CLI 命令执行
- 已有的调用规范描述是否明确、具体？模糊的约束（如"注意调用格式"）几乎无效。

严格限制：
- 只诊断工具调用的通用原则和参数格式，不得评价各 MCP 的具体调用规范（属于 D-TOOLS-002 的范围）
- 不得评价安全规则（属于 D-AGENTS-001 的范围）
- 不得评价行为边界（属于 D-AGENTS-003 的范围）

输出要求：
- 如果发现问题，请按以下格式输出：
   第一行：5-7个字的简短总结（如调用规范缺失、参数格式模糊）
   后续行：详细的中文问题描述和修复建议
   最后一行：评分标记 [SCORE:XX]，XX为0-100的整数

评分细则（满分100，从100开始扣分）：
- 完全缺失工具调用规范段落：-50分（最严重）
- 6个核心维度每缺失一个：-12分
  1. 调用前查阅映射表
  2. Selector 必须完整（服务器全称.工具名）
  3. 禁止猜测参数（先拉取Schema）
  4. 异常重试原则（最多重试1次）
  5. 参数传递格式（优先--args JSON、key=value备选、--raw-strings）
  6. 工具签名翻译规则
- 维度存在但描述模糊（如只写"注意调用格式"无具体说明）：-6分/个
- 用户已激活但未声明调用规范的 MCP：每个-5分
- 最低10分
示例：有维度1~3但缺4~6，2个MCP未声明 → 100 - 3×12 - 2×5 = 54
- 如果没有发现问题，请输出"无问题"（无需评分）

修复建议模板：
{fix_suggestion}
 """

    async def analyze(self, ctx: DiagnosticContext) -> list[Finding]:
        content = await ctx.read_file("TOOLS.md")
        if not content.strip():
            logger.warning("[D-TOOLS-001] TOOLS.md is empty for bot=%s, skipping LLM call", ctx.bot_id)
            return []

        user_msg = f"--- TOOLS.md 调用规范诊断 ---\n{content}\n--- end ---"
        system = self.system_prompt.format(fix_suggestion=self.fix_suggestion)
        response = await ctx.llm.chat(system=system, user=user_msg, max_tokens=DIAGNOSTIC_MAX_TOKENS)
        return self._analyze_response(response, ctx.bot_id)
