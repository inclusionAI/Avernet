"""The Harness composition root forwards every typed LLM setting."""

from dataclasses import dataclass

from agentclaw.community.di.config_community import CommunityLLMHarnessConfig
from agentclaw.community.di.modules.infrastructure.community.harness import (
    CommunityHarnessModule,
)


@dataclass
class _Secret:
    secret_user: str = "user"
    secret_value: str = "test-token"


class _Resolver:
    def get_secret(self, name: str) -> _Secret:
        assert name == "LLM_AUTH_TOKEN"
        return _Secret()


class _HttpClient:
    pass


def test_harness_llm_provider_forwards_model_and_timeout() -> None:
    llm = CommunityHarnessModule().llm(
        CommunityLLMHarnessConfig(
            base_url="https://llm.example/compatible-mode/v1",
            secret_name="LLM_AUTH_TOKEN",
            model="glm-5.2",
            timeout_ms=600_000,
        ),
        _Resolver(),
        _HttpClient(),
    )

    assert llm._model == "glm-5.2"
    assert llm._timeout_ms == 600_000
    assert llm._base_url.endswith("/compatible-mode")
