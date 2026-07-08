"""Coverage for the B11 Phase-A harness-LLM secret-resolver seam.

The harness ``LLM`` now reads its API token through an injected
``SecretResolver`` (corp=mist, community=env) instead of importing layotto
directly. These tests exercise the resolved-secret path and the None fallback.
"""
from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.harness.services.llm import LLM


@dataclass
class _Secret:
    secret_user: str
    secret_value: str


class _StubResolver:
    def __init__(self, secret):
        self._secret = secret

    def get_secret(self, name):
        return self._secret


def test_llm_loads_token_from_secret_resolver():
    llm = LLM(
        base_url="http://llm.local",
        secret_name="llm-key",
        secret_resolver=_StubResolver(_Secret(secret_user="u", secret_value="tok-xyz")),
    )
    assert llm._token == "tok-xyz"


def test_llm_falls_back_to_env_when_resolver_returns_none(monkeypatch):
    monkeypatch.setenv("LLM_AUTH_TOKEN", "env-tok")
    llm = LLM(
        base_url="http://llm.local",
        secret_name="llm-key",
        secret_resolver=_StubResolver(None),
    )
    assert llm._token == "env-tok"
