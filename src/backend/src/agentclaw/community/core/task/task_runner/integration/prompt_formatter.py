"""默认 PromptFormatter + _RunnerContextBuilder。

零 case:仅消费 _build_context dict 字段(mode/node_instruction/goal/...) + node.task_spec,不写节点名。
"""
from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.task.domain.prompt_constants import NO_WEB_SEARCH_CONSTRAINT

from agentclaw.community.core.task.domain.models import TaskNode
from agentclaw.community.core.task.task_runner.integration.ports import (
    PromptFormatter, TaskContextBuilder,
)


class PromptFormatterImpl(PromptFormatter):
    def format_execute(self, context: dict[str, Any], node: TaskNode) -> str:
        instr = context.get("node_instruction") or node.task_spec.metadata.instruction
        goal = node.task_spec.goal.objective
        siblings = context.get("sibling_outputs") or {}
        acceptances = [
            {"id": acceptance.id, "description": acceptance.description}
            for acceptance in node.task_spec.goal.acceptances
        ]
        parts = [
            "[task-execute]",
            "请执行任务。执行完成后，必须调用 task-loop 中的任务验收(acceptance)逻辑，逐条检查验收标准并上报结果。",
            f"目标:{goal}",
            f"指令:{instr}",
            f"验收标准:{json.dumps(acceptances, ensure_ascii=False)}",
        ]
        if siblings:
            parts.append(f"上游产出:{json.dumps(siblings, ensure_ascii=False, default=str)}")
        parts.append(
            "验收完成后，最终只能输出一个 JSON 对象，不要输出 Markdown 代码块或额外解释。"
            "通过示例:{\"success\":true,\"data\":{\"result\":\"任务实际产出\"},\"gaps\":[]};"
            "未通过示例:{\"success\":false,\"data\":{\"result\":\"当前已有产出\"},"
            "\"gaps\":[\"尚未满足的验收差距\"]}。"
        )
        parts.append(NO_WEB_SEARCH_CONSTRAINT)
        return "\n".join(parts)

    def format_verify(self, context: dict[str, Any], node: TaskNode) -> str:
        child_outputs = context.get("child_outputs") or {}
        acceptances = context.get("acceptances") or []
        acc = ";".join(a.description for a in acceptances)
        return f"验收标准:{acc}\n子产出:{child_outputs}\n\n{NO_WEB_SEARCH_CONSTRAINT}"


class _RunnerContextBuilder(TaskContextBuilder):
    def __init__(self, runner) -> None:
        self._runner = runner

    def build(self, task_id: str, node_id: str) -> dict[str, Any]:
        return self._runner._build_context(task_id, node_id)  # integration 内聚访问
