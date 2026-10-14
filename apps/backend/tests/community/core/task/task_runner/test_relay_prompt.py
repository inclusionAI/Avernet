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
    assert "BCS 会话文件读取凭证" not in prompt
    assert "Authorization" not in prompt


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
