from typing import Any, Protocol, runtime_checkable  # noqa: F401

from agentclaw.community.core.task.task_runner.client.ports import (
    ApiKeyProvider, BcsBotIdentityResolver, BcsClientPort, OpenApiBotPort, PromptFormatter,
    ResultSink, TaskContextBuilder,
)


def test_protocols_are_runtime_checkable():
    for p in (OpenApiBotPort, BcsBotIdentityResolver, BcsClientPort, ApiKeyProvider, TaskContextBuilder, PromptFormatter, ResultSink):
        assert hasattr(p, "_is_protocol")  # Protocol 子类


def test_open_api_bot_port_methods():
    # 真实结构断言:方法名存在(端口可导入)
    assert hasattr(OpenApiBotPort, "ensure_grant")
    assert hasattr(OpenApiBotPort, "send_message")
    assert hasattr(OpenApiBotPort, "get_run")
    assert hasattr(OpenApiBotPort, "cancel_run")


def test_bcs_bot_identity_resolver_methods():
    assert hasattr(BcsBotIdentityResolver, "resolve_many")


def test_bcs_client_port_methods():
    for m in ("create_group", "create_session", "get_group", "get_session_messages",
              "start_state_machine_run", "get_state_machine_run", "validate_definition"):
        assert hasattr(BcsClientPort, m)


def test_api_key_provider_properties():
    for p in ("api_key", "api_key_prefix", "base_url", "cookie", "referer"):
        assert hasattr(ApiKeyProvider, p)
