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


def _acceptance_instruction(context: dict[str, Any], *, task_id: str, node_id: str) -> str:
    """Build a non-skippable execution → acceptance → report protocol block."""
    backend = str(context.get("backend") or "{backend}")
    reporter = context.get("reporter_bot_id")
    reporter_line = (
        f"唯一上报者: reporter_bot_id={reporter}; reporter_role={context.get('reporter_role') or 'worker'}。"
        if reporter
        else "当前执行 Bot 是唯一上报者。"
    )
    return "\n".join([
        "【强制执行闭环，不得跳过】",
        "阶段1 执行：先完成上面的任务指令，形成完整执行产出。",
        "阶段2 校验：执行完成后，必须逐条对照当前 goal.acceptances，明确判断每条是否满足；不能只凭‘看起来完成’结束。",
        "阶段3 验收：整理完整 output，并生成 SUCCESS（全部满足）或 FAIL（存在未满足项）；FAIL 必须在 acceptance_result.gaps 中写明差距。",
        "阶段4 上报：必须真正发起 HTTP POST，不能只在对话中输出‘完成’或只返回 JSON。",
        reporter_line,
        f"回调地址: POST {backend}/api/v1/collaboration/tasks/callback/report",
        "请求体只能包含以下节点级字段；callback 内部会根据 task_id/node_id 组装回投关联字段：",
        json.dumps({
            "task_id": task_id,
            "node_id": node_id,
            "status": "SUCCESS",
            "output": "完整执行输出",
            "acceptance_result": {},
            "extend_props": {},
        }, ensure_ascii=False),
        "上报前自检：task_id/node_id 与当前节点一致；status 只能是 SUCCESS/FAIL；acceptance_result 和 extend_props 必须是对象；收到 HTTP 200 前不得认为上报完成。",
    ])


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
            "请严格按以下阶段执行，执行、校验、验收、上报均不可跳过。",
            f"目标:{goal}",
            f"指令:{instr}",
            f"验收标准:{json.dumps(acceptances, ensure_ascii=False)}",
            _acceptance_instruction(
                context,
                task_id=str(context.get("task_id") or node.task_id),
                node_id=str(context.get("node_id") or node.node_id),
            ),
        ]
        if siblings:
            parts.append(f"上游产出:{json.dumps(siblings, ensure_ascii=False, default=str)}")
        parts.append(
            "HTTP 上报完成后，回复中只需确认上报结果；不要用回复文本替代 HTTP POST。"
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
