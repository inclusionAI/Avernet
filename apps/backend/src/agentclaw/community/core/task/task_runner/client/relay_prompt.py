"""Instruction for an intake-created Relay baton using the fenced HTTP contract."""

from __future__ import annotations

import json
from typing import Any


def format_relay_execution_instruction(
    *, task_id: str, node_id: str, relay_token: str, backend_url: str,
    objective: str, instruction: str, acceptances: list[dict[str, Any]],
) -> str:
    """Give the Bot its current run generation, never a Bot session credential."""
    report_path = "/api/v1/collaboration/tasks/callback/report"
    context_path = f"/api/v1/collaboration/tasks/{task_id}/nodes/{node_id}/context"
    return "\n".join([
        "[task-execute]",
        "【分布式接力执行 S1-S6】当前节点是唯一执行范围；根目标和前序事实以 S1 快照为准。",
        f"task_id={task_id}; node_id={node_id}",
        f"relay_token={relay_token}（仅用于区分同一节点的执行代次，不是身份凭证）",
        f"backend_url={backend_url}（由 Backend 部署配置注入，不是身份凭证）",
        f"本节点目标：{objective}",
        f"本节点指令：{instruction}",
        f"本节点验收：{json.dumps(acceptances, ensure_ascii=False)}",
        "所有任务 HTTP 请求必须通过 task-loop Skill 的 scripts/relay_http.py 调用。"
        "该工具从 Bot 自己的 BCS 会话文件读取凭证，并使用本指令中由服务端配置注入的"
        " backend_url；每次调用必须传 --backend-url；"
        "不得把 BCS 凭证写入指令、请求 JSON、日志或聊天消息。",
        f"S1 读取上下文：GET {context_path}，请求头 X-Relay-Token 使用本次 relay_token。"
        "这是只读投影；all_done_output 只含本棒之前的已接纳事实。",
        "S2 根据 root_task_spec 与 all_done_output 在本地计算 task_gaps: list[str]。",
        "S3 根据 current_task_spec、task_gaps 和本 Bot 实际能力决定 actual_goal；"
        "不能覆盖时选择 DECLINED，不伪造执行事实。",
        "S4a 只执行 actual_goal，生成 TaskNodePatch 所需的 output_patch 与"
        " local_acceptance_result（verdict、done_items、gap_items）。",
        f"S4b POST {report_path}：event_type=EXECUTION_RESULT，"
        "event 为 {execution_decision, actual_goal, output_patch, local_acceptance_result, failure_reason}。"
        "顶层带 task_id、node_id、relay_token、event_id；event_id 为本事件新 UUID，"
        "同一 HTTP 请求超时后原样重试时必须复用。收到成功回执才进入 S5。",
        "S5.1 结合 S1 快照与本棒已接纳执行事实重算 task_gaps；"
        "S5.2 有 gaps 时构造唯一下一棒 TaskSpec，不能自行生成 node_id；"
        "S5.3 根据 gaps 与下一棒 TaskSpec 搜索候选；"
        "S5.4 本地选择单 Bot 或 BBS，并填充一个未落图 TaskNode 的 task_spec、"
        "run_info.run_mode 与 run_info.assignee；首版不选择协作群；"
        "S5.5 仅上报一次 PLAN_RESULT。若无 gaps，event=null 且提供通过的"
        " task_acceptance_result；有 gaps 则 event 为已选执行者的未落图 TaskNode"
        " {task_spec, run_info:{run_mode,assignee}}。上报仍带本次 relay_token 与新的 event_id；"
        "同一请求重试必须复用 event_id。保存回执中的 target_node_id。",
        "S6 若回执含 target_node_id，则 POST /api/v1/collaboration/tasks/dispatch，"
        "携带 task_id、origin_node_id、target_node_id、relay_token；"
        "Backend 调用 TaskDispatcher.dispatch(target_node) 真正投递。"
        "若任务已收口或挂起，则不调用 dispatch。",
        "出现 401、过期代次、状态冲突或投递失败时停止；不得换 Bot 身份、"
        "自行更新 token、猜测节点 ID 或将失败宣称成功。",
    ])
