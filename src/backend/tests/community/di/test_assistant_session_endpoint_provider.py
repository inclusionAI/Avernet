"""ConfigModule assistant-session endpoint provider.

The cron core formats links from an injected endpoint; the env-specific base URL
selection belongs in the composition/config layer.
"""
from __future__ import annotations

from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule


def test_assistant_session_endpoint_uses_prod_base(monkeypatch):
    monkeypatch.setattr(config_module, "get_current_env", lambda: "prod")
    monkeypatch.setenv(
        "TEAMCLAW_ASSISTANT_URL_BASE",
        "https://teamclaw.alipay.com/assistant",
    )
    monkeypatch.setenv(
        "TEAMCLAW_ASSISTANT_URL_BASE_PRE",
        "https://teamclaw-pre.alipay.com/assistant",
    )

    endpoint = ConfigModule().assistant_session_endpoint()

    assert endpoint.base_url == "https://teamclaw.alipay.com/assistant"


def test_assistant_session_endpoint_uses_pre_base(monkeypatch):
    monkeypatch.setattr(config_module, "get_current_env", lambda: "pre")
    monkeypatch.setenv(
        "TEAMCLAW_ASSISTANT_URL_BASE",
        "https://teamclaw.alipay.com/assistant",
    )
    monkeypatch.setenv(
        "TEAMCLAW_ASSISTANT_URL_BASE_PRE",
        "https://teamclaw-pre.alipay.com/assistant",
    )

    endpoint = ConfigModule().assistant_session_endpoint()

    assert endpoint.base_url == "https://teamclaw-pre.alipay.com/assistant"


def test_assistant_session_endpoint_pre_falls_back_to_prod_base(monkeypatch):
    monkeypatch.setattr(config_module, "get_current_env", lambda: "pre")
    monkeypatch.setenv(
        "TEAMCLAW_ASSISTANT_URL_BASE",
        "https://teamclaw.alipay.com/assistant",
    )
    monkeypatch.delenv("TEAMCLAW_ASSISTANT_URL_BASE_PRE", raising=False)

    endpoint = ConfigModule().assistant_session_endpoint()

    assert endpoint.base_url == "https://teamclaw.alipay.com/assistant"


def test_assistant_session_endpoint_uses_default_when_env_unset(monkeypatch):
    monkeypatch.setattr(config_module, "get_current_env", lambda: "prod")
    monkeypatch.delenv("TEAMCLAW_ASSISTANT_URL_BASE", raising=False)
    monkeypatch.delenv("TEAMCLAW_ASSISTANT_URL_BASE_PRE", raising=False)

    endpoint = ConfigModule().assistant_session_endpoint()

    assert endpoint.base_url == "https://teamclaw.example.com/assistant"
