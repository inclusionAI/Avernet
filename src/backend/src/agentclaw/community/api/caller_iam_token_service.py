"""Application boundary for IAM-token Caller identity updates."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.caller_identity.contracts import CallerIdentityStage
from agentclaw.community.plugin_api.auth import AuthRequestContext


class CallerIamTokenResult:
    """HTTP-neutral result; the Caller credential is never returned."""

    def __init__(self, *, iam_token: str, error: str | None = None, status_code: int = 200) -> None:
        self.iam_token = iam_token
        self.error = error
        self.status_code = status_code


@runtime_checkable
class CallerIamTokenServiceProtocol(Protocol):
    async def get_iam_token(
        self,
        *,
        iam_token: str,
        auth_request: AuthRequestContext,
        bot_id: str | None,
        stage: CallerIdentityStage,
        publish_id: int | None,
        entity_id: str | None,
        is_test_exchange: bool,
    ) -> CallerIamTokenResult: ...

