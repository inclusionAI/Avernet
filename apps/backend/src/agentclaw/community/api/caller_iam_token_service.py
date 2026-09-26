"""Application boundary for IAM-token Caller identity updates.

Re-export only. The Protocol is defined in its owning core module
(``core/caller_identity/caller_iam_token_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.caller_identity.caller_iam_token_service_protocol import (
    AuthRequestContext,
    CallerIamTokenOutcome,
    CallerIamTokenServiceProtocol,
    CallerIdentityStage,
)

__all__ = [
    "AuthRequestContext",
    "CallerIamTokenOutcome",
    "CallerIamTokenServiceProtocol",
    "CallerIdentityStage",
]
