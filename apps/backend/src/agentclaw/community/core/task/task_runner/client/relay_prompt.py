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
        "失败记录必须能还原关键问题现场，禁止只写执行失败/预算耗尽短语；"
        "至少包含 failure_type、stopped_reason、budget 实际值与阈值、"
        "actual_goal_digest、last_operation、raw_error_excerpt、"
        "evidence_refs、impacted_acceptances、impact_and_next_hint。"
        "只记录一个最相关的触发操作；单份失败 JSON 不超过 2000 字符，"
        "raw_error_excerpt 不超过 500 字符，evidence_refs 最多 5 个；"
        "发生裁剪时增加 truncation 并保留必需键，"
        "不得转储完整日志、会话、Prompt或全部产物。"
        "DECLINED 时 event.failure_reason 与 S4A_EXECUTION.error_msg 写同一份"
        "紧凑 JSON 现场记录；ACCEPTED 但 verdict=FAILED 时顶层 failure_reason "
        "仍为 null，详细现场写入 S4A trace 与 gap_items[].summary。"
        "失败现场不得包含 relay_token、密钥、会话凭证或完整原始日志。",
        f"S4b POST {report_path}：event_type=EXECUTION_RESULT，"
        "event 为 {execution_decision, actual_goal, output_patch, local_acceptance_result, failure_reason}。"
        "ACCEPTED 必须带 actual_goal 和 local_acceptance_result；"
        "DECLINED 不得带 actual_goal、local_acceptance_result 或非空 output_patch，"
        "且必须带 failure_reason。"
        "顶层带 task_id、node_id、relay_token、event_id；event_id 为本事件新 UUID，"
        "同一 HTTP 请求超时后原样重试时必须复用。只有解析到 data.status=APPLIED"
        "才进入 S5；失败、空 stdout 或未完成子会话都不是成功。",
        "S5.1 结合 S1 快照与本棒已接纳执行事实重算 task_gaps；"
        "BBS 竞价/认领消息、群聊回复或成员声明“任务已完成/验收通过”"
        "只是能力或意向，不是已接纳事实，不能据此 DECLINED 转 ACCEPTED，"
        "也不能据此提交 root task 的 task_acceptance_result。"
        "task_acceptance_result 只验收 root task，"
        "只有 refresh_task_gaps=[] 时才允许非空；"
        "done_items 必须为 {id,passed,summary} 并逐项覆盖 root_task_spec.goal.acceptances，"
        "收口必须 verdict=DONE 且 gap_items=[]；不得复制节点级 local_acceptance_result。"
        "S5.2 有 gaps 时构造唯一下一棒 TaskSpec，不能自行生成 node_id；"
        "S5.3 根据 gaps 与下一棒 TaskSpec 搜索候选，POST /api/v1/collaboration/tasks/search，"
        "stdin JSON 必须是 {query,task_id,node_id,relay_token}；"
        "422 是请求契约错误，不是 candidates=[]，禁止由此跳过 S5c。"
        "S5.4 本地选择 HIT_SINGLE、HIT_MULTI_BOTS 或 MISS。"
        "HIT_SINGLE 填 run_info.run_mode=single_bot 和 run_info.assignee=bot_id；"
        "HIT_MULTI_BOTS 填 run_mode=coop_group、assignee=null，并在"
        " run_info.extend_props.pending_group_formation 携带 manager_worker 的"
        " bot_ids、group_name、members_info 与 extend_props.manager_bot_id，"
        "不得传已有 group_id；MISS 填 run_mode=bbs、assignee=null，"
        "并把原因写入 run_info.failure_reason。"
        "S5.5 仅上报一次 PLAN_RESULT，三种分支恰好命中一个："
        "收口时 event=null 且提供通过的 task_acceptance_result；"
        "有后继时 event 为未落图 TaskNode"
        " {task_spec, run_info:{run_mode,assignee,failure_reason,extend_props}}，"
        "不得预填 node_id/status/node_run_graph；规划失败时 event=null"
        "并把原因写入顶层 failure_reason，禁止新增 planning_failure_reason 字段。"
        "上报仍带本次 relay_token 与新的 event_id；"
        "同一请求重试必须复用 event_id。只有 data.status=APPLIED 才进入 S6，"
        "并保存 target_node_id、task_completed、task_hung、bbs_publish_pending。",
        "S6 只有在 target_node_id 非空且 bbs_publish_pending=false 时，"
        "才 POST /api/v1/collaboration/tasks/dispatch，携带"
        " task_id、origin_node_id、target_node_id、relay_token；"
        "Backend 调用 TaskDispatcher.dispatch(target_node) 真正投递。"
        "task_completed=true、task_hung=true 或 bbs_publish_pending=true 时"
        "不得调用 dispatch，分别记录 task_completed、task_hung、"
        "bbs_publish_pending。",
        "本 Runtime 为保证观测，每次 report 请求都必须带顶层 trace={steps:[...]}；"
        "HTTP 契约允许省略 trace，但本 Runtime 不得省略。"
        "EXECUTION_RESULT.trace.steps 必须按顺序恰好 5 条："
        "S1_CONTEXT、S2_GAPS、S3_CAPABILITY、S4A_EXECUTION、S4B_REPORT。"
        "PLAN_RESULT.trace.steps 必须按顺序恰好 5 条："
        "S5A_REFRESH_GAPS、S5B_PLAN_NEXT_NODE、S5C_SEARCH_CANDIDATES、"
        "S5D_SELECT_EXECUTOR、S5E_REPORT_PLAN。"
        "每个 step 只能直接包含 7 个显式键：action_type、action_input、"
        "action_result、status_from、status_to、boost_reason、error_msg；"
        "允许为 null 的键也必须显式传 null。action_type 和 action_result "
        "必须是非空字符串。禁止 step/status/detail 或下挂 detail 的旧结构。"
        "action_input 必须是字符串或 null；结构化内容先序列化为紧凑 JSON 文本；"
        "它记录该步骤的输入事实、请求参数、候选、上下文或关键摘要。"
        "action_result 记录该步骤的推理输出内容，必须序列化为紧凑 JSON object "
        "字符串，并包含非空 status 字段；completed/failed/ready/not_applicable "
        "等状态值只能作为 JSON 内的 status 字段，JSON 还必须至少包含一个"
        "实际输出字段（例如结论、候选、产物、响应摘要或缺口）。"
        "status_from/status_to 记录本步骤前后的节点状态；"
        "boost_reason 记录执行过程证据：为什么推进、为什么选择等等维度信息；"
        "error_msg 记录失败原因，成功时填 null。"
        "S1_CONTEXT 记录 context 恢复状态与关键上下文摘要；S2_GAPS 记录根验收、"
        "已接纳前序事实与 gap 映射；S3_CAPABILITY 记录能力判定输入、actual_goal 与"
        "不能覆盖的原因；S4A_EXECUTION 记录执行计划、关键工具调用摘要、产物路径、"
        "输出结构、验收映射与关键证据；S4B_REPORT/S5E_REPORT_PLAN 记录请求分支、"
        "event_id、HTTP status 与响应结果；S5B_PLAN_NEXT_NODE 记录下一棒"
        " TaskSpec 的目标/验收/输入依赖；S5C_SEARCH_CANDIDATES 记录搜索词、"
        "raw_item_count、候选评分与 failed_keywords；S5D_SELECT_EXECUTOR 记录"
        "候选输入、选择原因、run_mode/assignee 与被排除候选原因。"
        "S6 不进入 report trace；dispatch 的服务端动作日志由 S6 单独记录。"
        "如果 S6 因权限适配将 single_bot 退化为 manager-only manager_worker 群，"
        "该事实由服务端 S6 的 actual_run_mode/group_id 记录，不算 S5D 选择失败。"
        "trace 只放事实摘要与证据引用，不放 relay_token、密钥、会话凭证或"
        "完整原始日志；trace JSON 在构造后固定，网络重试时必须与原请求字节一致。"
        "HTTP 422 表示契约非法：修正同一请求，禁止换 event_id；"
        "HTTP 409 表示状态冲突或执行代次失效：停止并重新读取 S1，"
        "禁止换 event_id 或 token；网络超时可原样重试同一 JSON 和 event_id。"
        "出现内网访问失败、过期代次、状态冲突或投递失败时停止；"
        "不得自行更新 token、猜测节点 ID 或将失败宣称成功。",
    ])
