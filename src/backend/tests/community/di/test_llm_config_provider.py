"""Harness LLM settings are normalized at the configuration boundary."""

import pytest

from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.infrastructure.community.harness import (
    CommunityHarnessModule,
)


def test_llm_harness_reads_model_and_string_timeout(monkeypatch) -> None:
    monkeypatch.setattr(
        config_module,
        "_user_config",
        lambda: {
            "llm": {
                "base_url": "https://llm.example/compatible-mode/v1",
                "secret_name": "LLM_AUTH_TOKEN",
                "model": "glm-5.2",
                "timeout_ms": "600000",
            }
        },
    )

    llm = CommunityHarnessModule().llm_config()

    assert llm.base_url == "https://llm.example/compatible-mode/v1"
    assert llm.secret_name == "LLM_AUTH_TOKEN"
    assert llm.model == "glm-5.2"
    assert llm.timeout_ms == 600_000


def test_llm_harness_absent_block_keeps_existing_defaults(monkeypatch) -> None:
    monkeypatch.setattr(config_module, "_user_config", lambda: {})

    llm = CommunityHarnessModule().llm_config()

    assert llm.base_url == ""
    assert llm.secret_name == "LLM_AUTH_TOKEN"
    assert llm.model == "glm-5.2"
    assert llm.timeout_ms == 180_000


def test_llm_harness_rejects_invalid_timeout(monkeypatch) -> None:
    monkeypatch.setattr(
        config_module,
        "_user_config",
        lambda: {"llm": {"timeout_ms": "ten minutes"}},
    )

    with pytest.raises(ValueError, match="llm.timeout_ms must be an integer"):
        CommunityHarnessModule().llm_config()
