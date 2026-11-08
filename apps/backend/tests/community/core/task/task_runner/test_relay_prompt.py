from agentclaw.community.core.task.task_runner.client.relay_prompt import (
    format_relay_execution_instruction,
)


def test_relay_prompt_routes_relay_entry_through_task_loop() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
    )

    assert "S0 路由锁：本指令是 Backend 下发的分布式接力执行入口" in prompt
    assert "必须由 task-loop Skill 承接" in prompt
    assert "禁止任何业务 Skill 抢占入口或直接执行业务任务" in prompt
    assert "task-loop 是编排 Skill" in prompt
    assert "业务 Skill 只能在 S2/S3/S4/S5 中作为工具被 task-loop 调用" in prompt
    assert "S0 只做入口路由，不进入 trace.steps，也不新增 report event" in prompt
    assert "完成路由锁定后进入 S1" in prompt
    assert prompt.index("[task-execute]") < prompt.index("S0 路由锁")
    assert "S0_CONTEXT" not in prompt


def test_relay_prompt_uses_internal_network_without_bcs_credential() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
    )

    assert "生产内网白名单" in prompt
    assert "不读取或发送 BCS session token" in prompt
    assert "X-Relay-Token 使用本次 relay_token" in prompt
    assert "完整 JSON 必须从 stdin 传入" in prompt
    assert "禁止位置参数、data 包装或 POST --relay-token" in prompt
    assert "必须在当前执行会话直接执行并检查 stdout" in prompt
    assert "BCS 会话文件读取凭证" not in prompt
    assert "Authorization" not in prompt


def test_relay_prompt_pins_mode_and_recovery_invariants() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
    )

    assert "若 accepted_plan_report 非空，不重做 S2-S5" in prompt
    assert "bbs_publish_pending=false 才恢复 dispatch" in prompt
    assert "单 Bot 会话、动态协作群会话和 BBS 会话都不改变本节点" in prompt
    assert "群 ID、会话 ID 或群成员列表" in prompt
    assert "不能替代节点标识，也不能跳过 S5c 搜索" in prompt
    assert "stdin JSON 必须是 {query,task_id,node_id,relay_token}" in prompt
    assert "422 是请求契约错误，不是 candidates=[]" in prompt
    assert "不得预填 node_id/status/node_run_graph" in prompt


def test_relay_prompt_forbids_dispatch_for_terminal_and_bbs_plans() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
    )

    assert "S6 只有在 target_node_id 非空且 bbs_publish_pending=false" in prompt
    assert "task_completed=true、task_hung=true 或 bbs_publish_pending=true" in prompt
    assert "不得调用 dispatch" in prompt
    assert "HTTP 422 表示契约非法" in prompt
    assert "HTTP 409 表示状态冲突或执行代次失效" in prompt
    assert "禁止换 event_id 或 token" in prompt


def test_relay_prompt_pins_s4a_execution_budget() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
    )

    assert "S4a 预算硬边界" in prompt
    assert "wall_seconds=600" in prompt
    assert "max_tool_calls=30" in prompt
    assert "max_consecutive_failures=5" in prompt
    assert "到达任一上限立即停止业务调用" in prompt
    assert "并继续 S4b 上报" in prompt
    assert "禁止卡住不切下一步" in prompt
    assert "失败记录必须能还原关键问题现场" in prompt
    assert "failure_type" in prompt
    assert "actual_goal_digest" in prompt
    assert "last_operation" in prompt
    assert "raw_error_excerpt" in prompt
    assert "evidence_refs" in prompt
    assert "impacted_acceptances" in prompt
    assert "impact_and_next_hint" in prompt
    assert "只记录一个最相关的触发操作" in prompt
    assert "单份失败 JSON 不超过 2000 字符" in prompt
    assert "raw_error_excerpt 不超过 500 字符" in prompt
    assert "evidence_refs 最多 5 个" in prompt
    assert "发生裁剪时增加 truncation 并保留必需键" in prompt
    assert "不得转储完整日志、会话、Prompt或全部产物" in prompt
    assert "DECLINED 时 event.failure_reason 与 S4A_EXECUTION.error_msg 写同一份" in prompt
    assert "ACCEPTED 但 verdict=FAILED 时顶层 failure_reason 仍为 null" in prompt
    assert "不得包含 relay_token、密钥、会话凭证或完整原始日志" in prompt


def test_relay_prompt_forbids_direct_web_tools() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
    )

    assert "禁止直接调用 web_search、web_fetch" in prompt
    assert "运行时工具存在性不能解除该限制" in prompt
    assert "不得把它们计入能力覆盖" in prompt
    assert "已激活的搜索 Skill 规定入口" in prompt


def test_relay_prompt_pins_acceptance_wire_contract() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[{"id": "ac1", "description": "覆盖根任务"}],
    )

    assert "verdict 只能是 DONE 或 FAILED" in prompt
    assert "禁止 PASS/FAIL" in prompt
    assert "{id,passed,summary}" in prompt
    assert "task_acceptance_result 只验收 root task" in prompt
    assert "不是已接纳事实" in prompt
    assert "不能据此提交 root task 的 task_acceptance_result" in prompt
    assert "只有 refresh_task_gaps=[] 时才允许非空" in prompt
    assert "收口必须 verdict=DONE 且 gap_items=[]" in prompt
    assert "不得复制节点级 local_acceptance_result" in prompt
    assert "顶层 failure_reason" in prompt
    assert "禁止新增 planning_failure_reason 字段" in prompt


def test_relay_prompt_requires_structured_diagnostic_trace() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
    )

    assert "每次 report 请求都必须带顶层 trace={steps:[...]}" in prompt
    assert "HTTP 契约允许省略 trace" in prompt
    assert "本 Runtime 不得省略" in prompt
    assert "EXECUTION_RESULT.trace.steps 必须按顺序恰好 5 条" in prompt
    assert "S1_CONTEXT、S2_GAPS、S3_CAPABILITY、S4A_EXECUTION、S4B_REPORT" in prompt
    assert "PLAN_RESULT.trace.steps 必须按顺序恰好 5 条" in prompt
    assert (
        "S5A_REFRESH_GAPS、S5B_PLAN_NEXT_NODE、S5C_SEARCH_CANDIDATES、"
        in prompt
    )
    assert "S5D_SELECT_EXECUTOR、S5E_REPORT_PLAN" in prompt
    assert "action_type、action_input、action_result、status_from、status_to、boost_reason、error_msg" in prompt
    assert "允许为 null 的键也必须显式传 null" in prompt
    assert "action_type 和 action_result 必须是非空字符串" in prompt
    assert "action_input 必须是字符串或 null" in prompt
    assert "action_result 记录该步骤的推理输出内容" in prompt
    assert "必须序列化为紧凑 JSON object" in prompt
    assert "包含非空 status 字段" in prompt
    assert "等状态值只能作为 JSON 内的 status 字段" in prompt
    assert "boost_reason 记录执行过程证据：为什么推进、为什么选择等等维度信息" in prompt
    assert "结构化内容先序列化为紧凑 JSON 文本" in prompt
    assert "禁止 step/status/detail 或下挂 detail 的旧结构" in prompt
    assert "step.detail" not in prompt
    assert "action_type=execute" not in prompt
    assert "action_type=plan" not in prompt
    assert "S6 不进入 report trace" in prompt
    assert "S4A_EXECUTION 记录执行计划、关键工具调用摘要、产物路径" in prompt
    assert "S5C_SEARCH_CANDIDATES 记录搜索词、raw_item_count、候选评分" in prompt
    assert "manager-only manager_worker 群" in prompt
    assert "S5D 选择失败" in prompt
    assert "不放 relay_token、密钥、会话凭证" in prompt
    assert "trace JSON 在构造后固定" in prompt


def test_relay_delivery_prompt_uses_required_trace_contract() -> None:
    from agentclaw.community.core.task.task_runner.client.prompt_formatter import (
        format_task_node_business_instruction,
    )

    prompt = format_task_node_business_instruction(
        task_id="task-1",
        node_id="node-1",
        backend="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
        relay_execution=True,
        relay_token="generation-1",
    )

    assert "S0 路由锁：本指令是 Backend 下发的分布式接力执行入口" in prompt
    assert "必须由 task-loop Skill 承接" in prompt
    assert "业务 Skill 只能在 S2/S3/S4/S5 中作为工具被 task-loop 调用" in prompt
    assert "S0 只做入口路由，不进入 trace.steps，也不新增 report event" in prompt

    assert "每次 report 请求都必须带顶层 trace={steps:[...]}" in prompt
    assert "HTTP 契约允许省略 trace" in prompt
    assert "本 Runtime 不得省略" in prompt
    assert "EXECUTION_RESULT.trace.steps" in prompt
    assert "PLAN_RESULT.trace.steps" in prompt
    assert "禁止 step/status/detail 或下挂 detail 的旧结构" in prompt
    assert "backend_url=https://backend.example" in prompt
