from agentclaw.community.core.task.task_runner.client.relay_prompt import (
    format_relay_execution_instruction,
)


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

    assert "每个 step.detail 都必须是结构化 JSON object" in prompt
    assert "至少记录该步骤的 input 与 output" in prompt
    assert "error={message,stage,retryable}" in prompt
    assert "S4a 记录执行计划、关键工具调用摘要、产物路径" in prompt
    assert "S5.3 记录搜索词、raw_item_count、候选评分" in prompt
    assert "需要核验时记录 evidence=[{type,source,summary,reference}]" in prompt
    assert "manager-only manager_worker 群" in prompt
    assert "不放 relay_token、密钥、会话凭证" in prompt
    assert "trace JSON 在构造后固定" in prompt
    assert "网络重试时必须与原请求字节一致" in prompt
