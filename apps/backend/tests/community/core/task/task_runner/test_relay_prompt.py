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
    assert "成功响应中的 data.handoff_display 是当前棒 S6 用户可见交接卡片的数据" in prompt
    assert "当且仅当 source_channel=dingtalk 且 data.handoff_display 非空时" in prompt
    assert "必须先通过 Runtime message 工具单独投递该模板卡片" in prompt
    assert "不调用 message 工具" in prompt
    assert "继续保持普通会话回复逻辑不变" in prompt
    assert "不得在会话回复与旁路消息之间重复发送" in prompt
    assert "HTTP 422 表示契约非法" in prompt
    assert "HTTP 409 表示状态冲突或执行代次失效" in prompt
    assert "禁止换 event_id 或 token" in prompt
    assert "S5a 结合" in prompt
    assert "S5b 有 gaps" in prompt
    assert "S5c 根据" in prompt
    assert "S5d 本地" in prompt
    assert "S5e 仅上报" in prompt
    assert "S5.1" not in prompt
    assert "S5.2" not in prompt
    assert "S5.3" not in prompt
    assert "S5.4" not in prompt
    assert "S5.5" not in prompt


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
    assert "失败说明只保留关键失败原因、受影响验收项、必要证据引用" in prompt
    assert "不为归因分析另行构造执行轨迹或重复现场 JSON" in prompt
    assert "DECLINED 将能力不足原因写入 event.failure_reason" in prompt
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


def test_relay_prompt_keeps_trace_optional_and_business_facts_required() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
    )

    assert "分布式接力不要求构造或上报 trace" in prompt
    assert "通用上报接口仍支持可选 trace" in prompt
    assert "不依赖 Bot 自报轨迹" in prompt
    assert "网络重试必须原样复用" in prompt
    assert "本 Runtime 不得省略" not in prompt
    assert "EXECUTION_RESULT.trace.steps" not in prompt
    assert "PLAN_RESULT.trace.steps" not in prompt
    assert "S4A_EXECUTION.error_msg" not in prompt
    assert "原样记入 S5D 的输入" not in prompt
    assert "actual_goal_digest" not in prompt
    assert "gap_items[].summary" in prompt
    assert "排除当前执行者和 S1 used_executor_ids" in prompt


def test_relay_delivery_prompt_uses_optional_trace_contract() -> None:
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

    assert "分布式接力不要求构造或上报 trace" in prompt
    assert "通用上报接口仍支持可选 trace" in prompt
    assert "本 Runtime 不得省略" not in prompt
    assert "backend_url=https://backend.example" in prompt



def test_relay_prompt_defaults_to_all_run_modes() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
    )

    assert "S5d后继允许执行模态=[\"single_bot\", \"coop_group\", \"bbs\"]" in prompt


def test_relay_prompt_injects_allowed_run_modes() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
        allowed_run_modes=["single_bot", "coop_group"],
    )

    assert "S5d后继允许执行模态=[\"single_bot\", \"coop_group\"]" in prompt
    assert "只能从该列表中选择下一棒 run_mode" in prompt
    assert "HIT_MULTI_BOTS 或 MISS" in prompt
    assert "当 bbs 在白名单内时，MISS 才可填 run_mode=bbs" in prompt
    assert "bbs_disabled_and_no_candidate" in prompt


def test_relay_prompt_injects_task_source_channel() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
        source_channel="dingtalk",
    )

    assert "source_channel=dingtalk" in prompt
    assert "该值是任务入口捕获的用户原始消息渠道" in prompt
    assert "不是当前 BCS 运行会话渠道" in prompt


def test_relay_prompt_treats_missing_source_channel_as_unknown() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
    )

    assert "source_channel=unknown" in prompt
    assert "source_channel=dingtalk 且" in prompt
    assert "不调用 message 工具" in prompt


def test_relay_prompt_injects_non_dingtalk_source_channel() -> None:
    prompt = format_relay_execution_instruction(
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
        backend_url="https://backend.example",
        objective="完成任务",
        instruction="执行并上报",
        acceptances=[],
        source_channel="webchat",
    )

    assert "source_channel=webchat" in prompt
    assert "source_channel=dingtalk 且" in prompt
