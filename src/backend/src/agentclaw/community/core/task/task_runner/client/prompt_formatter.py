"""默认 PromptFormatter + _RunnerContextBuilder。

零 case:仅消费 _build_context dict 字段(mode/node_instruction/goal/...) + node.task_spec,不写节点名。
"""
from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.task.domain.prompt_constants import NO_WEB_SEARCH_CONSTRAINT

from agentclaw.community.core.task.domain.models import TaskNode
from agentclaw.community.core.task.task_runner.client.ports import (
    PromptFormatter, TaskContextBuilder,
)


def _skill_report_instruction(context: dict[str, Any], *, task_id: str, node_id: str) -> str:
    """skill HTTP callback protocol block for task-node result reporting.

    Only emitted when ``skill_report_enabled`` is true. The disabled path must
    not describe the platform's pull protocol to the model; the platform owns
    result collection in that mode.
    """
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
        "阶段3 验收：整理完整 output；验收全部满足则 status=SUCCESS，否则 status=DONE，并在 acceptance_result.gaps 写明差距；只有执行失败才使用 status=FAILED。",
        "阶段4 上报：必须真正发起 HTTP POST，不能只在对话中输出‘完成’或只返回 JSON。",
        reporter_line,
        f"回调地址: POST {backend}/api/v1/collaboration/tasks/callback/report",
        "请求体只能包含以下节点级字段，不要增加 loop_task_id/workflow_type/workflow_id/instance_id/result 包装：",
        json.dumps({
            "task_id": task_id,
            "node_id": node_id,
            "status": "SUCCESS",
            "output": "完整执行输出",
            "acceptance_result": {
                "verdict": "DONE",
                "acceptances_metric": [
                    {"id": "验收项ID", "passed": True, "summary": "满足原因"}
                ],
                "gaps": [],
            },
            "extend_props": {},
        }, ensure_ascii=False),
        "上报前自检：task_id/node_id 必须使用当前节点值；status 只能是 SUCCESS(验收通过)/DONE(验收未通过)/FAILED(执行失败)；验收通过时 verdict=DONE 且 gaps=[]，验收未通过时 verdict=FAILED 且 gaps 非空；acceptances_metric 必须是数组，不要改成 {验收项ID:{passed,summary}} 映射；收到 HTTP 200 前不得认为上报完成。",
    ])


def _no_callback_instruction() -> str:
    """Boundary-only instruction for platform-managed result collection.

    Deliberately omits the platform's pull payload schema so the model does
    not confuse an internal collection format with business output.
    """
    return "本节点结果由平台接口负责回收；不要主动调用 /callback/report，不要构造或发送节点级 Push 请求。"


class PromptFormatterImpl(PromptFormatter):
    def format_execute(self, context: dict[str, Any], node: TaskNode) -> str:
        instr = context.get("node_instruction") or node.task_spec.metadata.instruction
        # 接力交接节点:instruction 由 static_plan_runtime._decorate 注入 "# 接自 ..." 三段
        # (# 接自 / ## 群组成 / ## 上游产出正文 / ## 本角色任务)。此时直接下发交接正文即可——
        # bot 收到的是"从 X 接过来一个任务,情况是…",不再套派单/目标/验收/回收协议/字段要求/禁联网;
        # 结果回收与验收由各 bot 的 skill/rule 和平台回收机制承托。
        if str(instr).lstrip().startswith("# 接自"):
            protocol = (
                _skill_report_instruction(
                    context,
                    task_id=str(context.get("task_id") or node.task_id),
                    node_id=str(context.get("node_id") or node.node_id),
                )
                if context.get("skill_report_enabled", True)
                else _no_callback_instruction()
            )
            return f"{instr.rstrip()}\n{protocol}"
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
        ]
        # 所有执行模式统一由 skill_report_enabled 决定是否主动 callback；
        # 关闭时只告诉 Bot 不要主动 callback，平台内部回收格式不注入 prompt。
        if context.get("skill_report_enabled", True):
            parts.append(_skill_report_instruction(
                context,
                task_id=str(context.get("task_id") or node.task_id),
                node_id=str(context.get("node_id") or node.node_id),
            ))
            parts.append("HTTP 上报完成后，回复中只需确认上报结果；不要用回复文本替代 HTTP POST。")
        else:
            parts.append(_no_callback_instruction())
        if siblings:
            parts.append(f"上游产出:{json.dumps(siblings, ensure_ascii=False, default=str)}")
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
