"""Singlebox-only Caller IAM token boundary."""

from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.api.caller_iam_token_service import (
    CallerIamTokenServiceProtocol,
)
from agentclaw.community.core.caller_identity.contracts import (
    CallerIamTokenOutcome,
    CallerIdentityStage,
)
from agentclaw.community.plugin_api.auth import AuthRequestContext


class SingleboxCallerIamTokenService:
    """Return an opaque token because singlebox has no corporate SSO cookie."""

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
    ) -> CallerIamTokenOutcome:
        return CallerIamTokenOutcome(iam_token="mock_iam_token")


class SingleboxCallerIdentityModule(Module):
    """Install the no-SSO Caller IAM boundary for the singlebox profile."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            CallerIamTokenServiceProtocol,
            to=SingleboxCallerIamTokenService,
            scope=singleton,
        )
