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


def _skill_report_instruction(context: dict[str, Any], *, task_id: str, node_id: str) -> str:
    """skill HTTP 上报协议块:bot 主动 POST /callback/report 回投(与 poller 互斥)。

    用于 coop_group / state_machine / 开了开关的 single_bot。status=验收通过 DONE / 不通过 FAILED;
    verdict 同步 DONE/FAILED;产出放 output 字符串。
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
        "阶段3 验收：整理完整 output；验收全部满足则 status=DONE，否则 status=FAILED，并在 acceptance_result.gaps 写明差距。",
        "阶段4 上报：必须真正发起 HTTP POST，不能只在对话中输出‘完成’或只返回 JSON。",
        reporter_line,
        f"回调地址: POST {backend}/api/v1/collaboration/tasks/callback/report",
        "请求体只能包含以下节点级字段；callback 内部会根据 task_id/node_id 组装回投关联字段：",
        json.dumps({
            "task_id": task_id,
            "node_id": node_id,
            "status": "DONE",
            "output": "完整执行输出",
            "acceptance_result": {},
            "extend_props": {},
        }, ensure_ascii=False),
        "上报前自检：task_id/node_id 与当前节点一致；status 只能是 DONE(通过)/ FAILED(不通过)；acceptance_result 和 extend_props 必须是对象；收到 HTTP 200 前不得认为上报完成。",
    ])


def _poller_content_instruction() -> str:
    """poller 拉取协议块:bot 在终态回复末尾产出 {success,data,gaps} JSON,供平台拉取回收(默认,与 HTTP 上报互斥)。"""
    return "\n".join([
        "【结果回收协议 — poller 拉取模式，不要 HTTP 上报】",
        "你最终回复的末尾必须包含一个 JSON 对象，供任务平台拉取回收；不要发起任何 HTTP POST。",
        "JSON 严格遵守以下字段名与类型：",
        json.dumps({
            "success": True,
            "data": "完整执行产出文本",
            "gaps": [],
        }, ensure_ascii=False),
        "字段要求：",
        "- success: 布尔值；验收全部满足为 true，任一不满足为 false。必须是布尔，不能是字符串 \"true\"/\"false\"。",
        "- data: 字符串；可直接阅读的结论/产出汇总文本，不是 http 响应原文，留空时给 \"\"。",
        "- gaps: 字符串数组；success=false 时必须非空，逐条写明未满足的验收标准项；success=true 时保持 []。",
        "阶段强制：先完成指令产出 → 逐条对照 goal.acceptances 校验 → 据校验结果填 success/gaps → 末尾输出该 JSON。",
        "注意：不要凭‘看起来完成’直接判 success=true；任一验收不满足都必须 success=false 并填 gaps。",
    ])


class PromptFormatterImpl(PromptFormatter):
    def format_execute(self, context: dict[str, Any], node: TaskNode) -> str:
        instr = context.get("node_instruction") or node.task_spec.metadata.instruction
        # 接力交接节点:instruction 由 static_plan_runtime._decorate 注入 "# 接自 ..." 三段
        # (# 接自 / ## 群组成 / ## 上游产出正文 / ## 本角色任务)。此时直接下发交接正文即可——
        # bot 收到的是"从 X 接过来一个任务,情况是…",不再套派单/目标/验收/回收协议/字段要求/禁联网;
        # 回收(末尾 JSON / HTTP 上报)与验收、禁联网由各 bot 的 skill/rule 配置保回流,
        # 框架兜底(single_bot poller / 80s auto-mock)承托,流程不卡。
        if str(instr).lstrip().startswith("# 接自"):
            return str(instr)
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
        mode = context.get("execution_mode")
        if mode == "single_bot":
            # single_bot 回收链路由开关决定(默认 poller 拉取;开启后走 skill HTTP 上报),两条链路互斥不并存。
            if context.get("single_bot_skill_report"):
                parts.append(_skill_report_instruction(
                    context,
                    task_id=str(context.get("task_id") or node.task_id),
                    node_id=str(context.get("node_id") or node.node_id),
                ))
                parts.append("HTTP 上报完成后，回复中只需确认上报结果；不要用回复文本替代 HTTP POST。")
            else:
                parts.append(_poller_content_instruction())
        else:
            # coop_group / state_machine / 其它:统一 skill HTTP 上报协议(回调驱动)。
            parts.append(_skill_report_instruction(
                context,
                task_id=str(context.get("task_id") or node.task_id),
                node_id=str(context.get("node_id") or node.node_id),
            ))
            parts.append("HTTP 上报完成后，回复中只需确认上报结果；不要用回复文本替代 HTTP POST。")
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
