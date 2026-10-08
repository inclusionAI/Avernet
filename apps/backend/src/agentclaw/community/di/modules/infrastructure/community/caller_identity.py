"""Community Caller-IAM boundary for deployments without Caller mode."""

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


CALLER_MODE_UNSUPPORTED_TOKEN = "caller_mode_unsupported"


class CommunityCallerIamTokenService:
    """Return an opaque placeholder while community runtimes lack Caller mode.

    The value only preserves the existing browser/provider contract.  It is not
    derived from, and must never expose, the OAuth session or another credential.
    """

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
        del (
            iam_token,
            auth_request,
            bot_id,
            stage,
            publish_id,
            entity_id,
            is_test_exchange,
        )
        return CallerIamTokenOutcome(iam_token=CALLER_MODE_UNSUPPORTED_TOKEN)


class CommunityCallerIdentityModule(Module):
    """Install the no-Caller implementation for the community profile only."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            CallerIamTokenServiceProtocol,
            to=CommunityCallerIamTokenService,
            scope=singleton,
        )


__all__ = [
    "CALLER_MODE_UNSUPPORTED_TOKEN",
    "CommunityCallerIamTokenService",
    "CommunityCallerIdentityModule",
]
