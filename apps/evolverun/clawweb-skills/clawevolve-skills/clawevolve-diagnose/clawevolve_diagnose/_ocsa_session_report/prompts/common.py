"""
通用 Prompt 片段

提供各评估器共用的 Prompt 片段，确保一致的输出格式和评估约束。
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════
#  JSON 输出格式约束（所有评估器共用）
# ═══════════════════════════════════════════════════════════

JSON_OUTPUT_CONSTRAINTS = """[输出格式 —— 绝对强制]
1. 你必须且只能输出合法的 JSON 格式，不得包含任何 JSON 之外的内容
2. 禁止输出：开头/结尾的说明文字、Markdown 代码块标记```、思考过程、额外解释
3. 输出的第一个字符必须是 {，最后一个字符必须是 }
4. 所有字符串必须用双引号包裹，禁止使用单引号
5. JSON 内禁止使用 Python 注释（#），禁止使用尾随逗号
6. 如需换行，必须在字符串值内部使用 \\n，或使用 JSON 数组/对象结构"""

JSON_OUTPUT_EXAMPLES = """[错误示例 — 绝对禁止]
```
好的，我来分析这个对话...
{"skills": [...]}
```
```json
{"skills": [...]}
```
分析完成。

[正确示例]
{"skills": [{"name": "xxx", "is_correct": 1, "execution": {"status": "success", "retry_detected": false, "retry_count": 0, "success_experience": null, "failure_category": null}}]}"""

# ═══════════════════════════════════════════════════════════
#  评估约束（所有评估器共用）
# ═══════════════════════════════════════════════════════════

EVALUATION_CONSTRAINTS = """[评估约束 —— 不可违背]
1. 任何无法从对话记录中直接验证的结论，必须输出 "unknown" 或 "UNKNOWN"
2. 不得基于工具名称推测其功能。判断必须从实际调用参数和结果出发
3. 不得假设存在未在对话中显式出现的信息
4. 禁止编造用户反馈、禁止推测对话之外的状态"""

# ═══════════════════════════════════════════════════════════
#  通用 System Prompt（用于 Session Report 评估）
# ═══════════════════════════════════════════════════════════

JUDGE_SYSTEM_PROMPT = f"""你是一个严格的对话评估专家。你的工作是审核 AI 助手在对话中的行为，输出结构化评估结果。

{JSON_OUTPUT_CONSTRAINTS}

{JSON_OUTPUT_EXAMPLES}

{EVALUATION_CONSTRAINTS}

5. 如果 toolResult 内容被截断或缺失，无法看到完整错误信息，failure_category 必须是 "UNKNOWN"
6. 如果无法区分"重试"和"正常的多步调用"，retry_detected 必须是 null，retry_count 必须是 null
7. 如果无法确认用户原始意图，is_correct 必须是 "unknown"
8. 问题分类码必须是 UPPER_SNAKE_CASE 格式，最多5个单词，必须从表面报错推断本质根因"""


# ═══════════════════════════════════════════════════════════
#  工厂函数：构建自定义 System Prompt
# ═══════════════════════════════════════════════════════════

def build_system_prompt(role_description: str, extra_constraints: str = "") -> str:
    """构建自定义 System Prompt

    Args:
        role_description: 角色描述（如"你是一个专业的 AI Agent Runtime 行为评估专家"）
        extra_constraints: 额外的评估约束，会追加到基础约束之后

    Returns:
        完整的 System Prompt
    """
    parts = [
        role_description,
        "",
        JSON_OUTPUT_CONSTRAINTS,
    ]

    if extra_constraints:
        parts.extend(["", EVALUATION_CONSTRAINTS, "", extra_constraints])
    else:
        parts.extend(["", EVALUATION_CONSTRAINTS])

    return "\n".join(parts)