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
