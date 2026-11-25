"""Instruction for an intake-created Relay baton using the fenced HTTP contract."""

from __future__ import annotations

import json
from typing import Any


def format_relay_execution_instruction(
    *, task_id: str, node_id: str, relay_token: str, backend_url: str,
    objective: str, instruction: str, acceptances: list[dict[str, Any]],
    allowed_run_modes: list[str] | None = None,
    source_channel: str | None = None,
) -> str:
    """Give the Bot its current run generation, never a Bot session credential."""
    report_path = "/api/v1/collaboration/tasks/callback/report"
    context_path = f"/api/v1/collaboration/tasks/{task_id}/nodes/{node_id}/context"
    modes = [
        str(mode).strip()
        for mode in (allowed_run_modes or ["single_bot", "coop_group", "bbs"])
        if str(mode).strip()
    ] or ["single_bot", "coop_group", "bbs"]
    channel = str(source_channel or "").strip().lower() or "unknown"
    return "\n".join([
        "[task-execute]",
        "S0 路由锁：本指令是 Backend 下发的分布式接力执行入口，必须由 task-loop Skill 承接；"
        "禁止任何业务 Skill 抢占入口或直接执行业务任务。task-loop 是编排 Skill，"
        "业务 Skill 只能在 S2/S3/S4/S5 中作为工具被 task-loop 调用。"
        "S0 只做入口路由，不进入 trace.steps，也不新增 report event；完成路由锁定后进入 S1。",
        "【分布式接力执行 S1-S6】当前节点是唯一执行范围；根目标和前序事实以 S1 快照为准。",
        f"task_id={task_id}; node_id={node_id}",
        f"relay_token={relay_token}（仅用于区分同一节点的执行代次，不是身份凭证）",
        f"backend_url={backend_url}（由 Backend 部署配置注入，不是身份凭证）",
        f"本节点目标：{objective}",
        f"本节点指令：{instruction}",
        f"本节点验收：{json.dumps(acceptances, ensure_ascii=False)}",
        f"source_channel={channel}；该值是任务入口捕获的用户原始消息渠道，"
        "不是当前 BCS 运行会话渠道，禁止反推或改写。",
        f"S5d后继允许执行模态={json.dumps(modes, ensure_ascii=False)}；"
        "只能从该列表中选择下一棒 run_mode，未列出的模态一律禁止；"
        "无可用候选且列表内模式都不可执行时，按规划失败上报，不得绕过该白名单。",
        "所有任务 HTTP 请求必须通过 task-loop Skill 的 scripts/relay_http.py 调用。"
        "该工具使用本指令中由服务端配置注入的 backend_url 访问生产内网白名单保护的"
        " /api/v1 接口；每次调用必须传 --backend-url；不读取或发送 BCS session token。"
        "GET context 的 relay_token 通过 --relay-token 传入；report/search/dispatch 的"
        "完整 JSON 必须从 stdin 传入，禁止位置参数、data 包装或 POST --relay-token；"
        "必须在当前执行会话直接执行并检查 stdout。",
        f"S1 读取上下文：GET {context_path}，请求头 X-Relay-Token 使用本次 relay_token。"
        "这是只读投影；all_done_output 只含本棒之前的已接纳事实。"
        "若 accepted_plan_report 非空，不重做 S2-S5：仅 target_node_id 非空且"
        " bbs_publish_pending=false 才恢复 dispatch；task_completed/task_hung/"
        " bbs_publish_pending 均不调用 dispatch。若仅 accepted_execution_patch 非空，"
        "复用该 patch 进入 S5；两者均空才从 S2 开始。"
        "单 Bot 会话、动态协作群会话和 BBS 会话都不改变本节点的"
        " task_id/node_id/relay_token/backend_url；群 ID、会话 ID 或群成员列表"
        "不能替代节点标识，也不能跳过 S5c 搜索。",
        "S2 根据 root_task_spec 与 all_done_output 在本地计算 task_gaps: list[str]。",
        "S3 根据 current_task_spec、task_gaps 和本 Bot 实际能力决定 actual_goal；"
        "不能覆盖时选择 DECLINED，不伪造执行事实。"
        "Relay 全程禁止直接调用 web_search、web_fetch 或任何底层联网搜索/抓取接口；"
        "AGENTS.md/IDENTITY.md/运行时工具存在性不能解除该限制，"
        "且不得把它们计入能力覆盖。联网检索只能通过画像明确授权"
        "且已激活的搜索 Skill 规定入口执行。",
        "S4a 只执行 actual_goal，生成 TaskNodePatch 所需的 output_patch 与"
        " local_acceptance_result；verdict 只能是 DONE 或 FAILED，禁止 PASS/FAIL，"
        "done_items/gap_items 对象必须为 {id,passed,summary}，"
        "二者 id 并集必须逐项且仅一次覆盖 actual_goal.acceptances。",
        "S4a 预算硬边界：wall_seconds=600、max_tool_calls=30、"
        "max_consecutive_failures=5；到达任一上限立即停止业务调用，"
        "形成明确的 DECLINED 或诚实 FAILED 验收事实，并继续 S4b 上报，"
        "禁止卡住不切下一步。"
        "失败说明只保留关键失败原因、受影响验收项、必要证据引用与下一步所需条件，"
        "不为归因分析另行构造执行轨迹或重复现场 JSON。"
        "DECLINED 将能力不足原因写入 event.failure_reason；"
        "ACCEPTED 但 verdict=FAILED 时顶层 failure_reason 仍为 null，"
        "实际失败事实与未满足验收项写入 output_patch 和 gap_items[].summary。"
        "不得包含 relay_token、密钥、会话凭证或完整原始日志。",
        f"S4b POST {report_path}：event_type=EXECUTION_RESULT，"
        "event 为 {execution_decision, actual_goal, output_patch, local_acceptance_result, failure_reason}。"
        "ACCEPTED 必须带 actual_goal 和 local_acceptance_result；"
        "DECLINED 不得带 actual_goal、local_acceptance_result 或非空 output_patch，"
        "且必须带 failure_reason。"
        "顶层带 task_id、node_id、relay_token、event_id；event_id 为本事件新 UUID，"
        "同一 HTTP 请求超时后原样重试时必须复用。只有解析到 data.status=APPLIED"
        "才进入 S5；失败、空 stdout 或未完成子会话都不是成功。",
        "S5a 结合 S1 快照与本棒已接纳执行事实重算 task_gaps；"
        "BBS 竞价/认领消息、群聊回复或成员声明“任务已完成/验收通过”"
        "只是能力或意向，不是已接纳事实，不能据此 DECLINED 转 ACCEPTED，"
        "也不能据此提交 root task 的 task_acceptance_result。"
        "task_acceptance_result 只验收 root task，"
        "只有 refresh_task_gaps=[] 时才允许非空；"
        "done_items 必须为 {id,passed,summary} 并逐项覆盖 root_task_spec.goal.acceptances，"
        "收口必须 verdict=DONE 且 gap_items=[]；不得复制节点级 local_acceptance_result。"
        "S5b 有 gaps 时构造唯一下一棒 TaskSpec，不能自行生成 node_id；"
        "S5c 根据 gaps 与下一棒 TaskSpec 搜索候选，POST /api/v1/collaboration/tasks/search，"
        "stdin JSON 必须是 {query,task_id,node_id,relay_token}；"
        "422 是请求契约错误，不是 candidates=[]，禁止由此跳过 S5c。"
        "S5c 必须先真实 /search，S5d 只使用该次响应中的候选做选择，不重复上报候选列表。"
        "S5d 本地先排除当前执行者和 S1 used_executor_ids 中的历史执行者（包括协作群成员），"
        "再在 S5d后继允许执行模态白名单内选择 HIT_SINGLE、HIT_MULTI_BOTS 或 MISS。"
        "无论任何来源（IDENTITY/AGENTS/工具矩阵或历史会话）都无法追加候选。"
        "HIT_SINGLE 填 run_info.run_mode=single_bot 和"
        " run_info.assignee=<当次S5C返回的BotCandidate.bot_id>，该候选使用外部权威身份形态，"
        "由 Backend 落图时拆分为产品bot_id与assignee_owner_id；"
        "HIT_MULTI_BOTS 填 run_mode=coop_group、assignee=null，"
        "并在 run_info.extend_props.pending_group_formation 携带 manager_worker 的"
        " bot_ids、group_name、members_info 与 extend_props.manager_bot_id，"
        "其中每个 bot_id 也必须来自同一候选响应，不得传已有 group_id；"
        "当 bbs 在白名单内时，MISS 才可填 run_mode=bbs、assignee=null，"
        "并把原因写入 run_info.failure_reason。bbs 不在白名单内时，"
        "MISS 必须转为规划失败：event=null、task_acceptance_result=null，"
        "并把 bbs_disabled_and_no_candidate 写入顶层 failure_reason。"
        "S5e 仅上报一次 PLAN_RESULT，三种分支恰好命中一个："
        "收口时 event=null 且提供通过的 task_acceptance_result；"
        "有后继时 event 为未落图 TaskNode"
        " {task_spec, run_info:{run_mode,assignee,failure_reason,extend_props}}，"
        "不得预填 node_id/status/node_run_graph；规划失败时 event=null"
        "并把原因写入顶层 failure_reason，禁止新增 planning_failure_reason 字段。"
        "终态 PLAN_RESULT(event=null) 必须提供非空 final_conclusion，说明实际结论；"
        "需用户补充输入时说明已知事实、待确认问题和继续执行所需条件，"
        "不通过 trace 证明收口合法性。"
        "上报仍带本次 relay_token 与新的 event_id；"
        "同一请求重试必须复用 event_id。只有 data.status=APPLIED 才进入 S6，"
        "并保存 target_node_id、task_completed、task_hung、bbs_publish_pending。",
        "S6 只有在 target_node_id 非空且 bbs_publish_pending=false 时，"
        "才 POST /api/v1/collaboration/tasks/dispatch，携带"
        " task_id、origin_node_id、target_node_id、relay_token；"
        "Backend 调用 TaskDispatcher.dispatch(target_node) 真正投递。"
        "task_completed=true、task_hung=true 或 bbs_publish_pending=true 时"
        "不得调用 dispatch，分别记录 task_completed、task_hung、"
        "bbs_publish_pending。"
        "成功响应中的 data.handoff_display 是当前棒 S6 用户可见交接卡片的数据；"
        "当且仅当 source_channel=dingtalk 且 data.handoff_display 非空时，"
        "必须先通过 Runtime message 工具单独投递该模板卡片，"
        "然后继续输出普通会话回复；source_channel=webchat、unknown 或缺失时，"
        "不调用 message 工具，也不生成、不外发转协作模板消息，"
        "继续保持普通会话回复逻辑不变。"
        "字段缺失时不得编造，也不得在会话回复与旁路消息之间重复发送。",
        "分布式接力不要求构造或上报 trace；S1-S6 的执行职责不变，"
        "正确性由业务字段和服务端任务状态、当前代次搜索回执及执行者身份校验，"
        "不依赖 Bot 自报轨迹。通用上报接口仍支持可选 trace，"
        "提供时沿用其结构校验、轨迹写入与 event_id 内容指纹语义。"
        "整个上报 JSON 构造后固定，网络重试必须原样复用；"
        "不得对已发送的同一 event_id 增删 trace 或改变其它内容。"
        "S6 由 dispatch 的服务端动作日志记录，不增加 report 事件。",
        "HTTP 422 表示契约非法：修正同一请求，禁止换 event_id；"
        "HTTP 409 表示状态冲突或执行代次失效：停止并重新读取 S1，"
        "禁止换 event_id 或 token；网络超时可原样重试同一 JSON 和 event_id。"
        "出现内网访问失败、过期代次、状态冲突或投递失败时停止；"
        "不得自行更新 token、猜测节点 ID 或将失败宣称成功。",
    ])
