"""Shared test stubs for task_experience conformance suites.

Reusable fake “HttpClient / SecretResolver / LLM / PolicyRetrieval /
BotTags” instances that the conformance suites breathe through their
``make_xxx`` factory arguments. They are intentionally minimal and never
touch the network.
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.task_experience.protocols import (
    BotTagsPort,
    PolicyRetrievalPort,
    TaskExperienceLLMProtocol,
)
from agentclaw.community.core.task.task_experience.types import (
    KGBasePolicy,
    TASK_EXPERIENCE_LLM_DISABLED,
)


# ── Http / Secret -----------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeHttpClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[tuple[str, Any]] = []

    def post(
        self,
        path: str,
        *,
        params: Any | None = None,
        json: Any | None = None,
        files: Any | None = None,
        data: Any | None = None,
        headers: Any | None = None,
        timeout: float = 30.0,
    ) -> _FakeResponse:
        self.calls.append((path, json))
        return _FakeResponse(self._payload)


class _FakeSecret:
    def __init__(self, secret_value: str | None) -> None:
        self.secret_value = secret_value


class _FakeSecretResolver:
    def __init__(self, token: str | None) -> None:
        self._token = token
        self.calls: list[str] = []

    def get_secret(self, name: str) -> Any | None:
        self.calls.append(name)
        if self._token is None:
            return None
        return _FakeSecret(self._token)


# ── Protocol stubs for higher-level suites ---------------------------------


class _DisabledLLM(TaskExperienceLLMProtocol):
    """LLM stub that always returns the disabled sentinel."""

    async def chat(self, *, system: str, user: str,
                   max_tokens: int = 2000, temperature: float = 0.1) -> str:
        return TASK_EXPERIENCE_LLM_DISABLED


class _NoopPolicyRetrieval(PolicyRetrievalPort):
    """Policy retrieval stub returning an empty list for every call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def retrieve(
        self,
        query: str,
        *,
        bot_id: str,
        entity_type: str = "Policy",
        property_name: str = "trigger",
        top_k: int = 5,
        min_score: float = 0.3,
        experience_type: str | None = None,
        active_only: bool = True,
    ) -> list[KGBasePolicy]:
        self.calls.append({"query": query, "bot_id": bot_id,
                           "experience_type": experience_type})
        return []


class _IdentityBotTags(BotTagsPort):
    def bot_name(self, bot_id: str) -> str:
        return bot_id


__all__ = [
    "_FakeResponse",
    "_FakeHttpClient",
    "_FakeSecret",
    "_FakeSecretResolver",
    "_DisabledLLM",
    "_NoopPolicyRetrieval",
    "_IdentityBotTags",
]
