"""The legacy approvals write.

PUT /openapi/v1/bots/approvals/{bot_id}/mode took ``session_key`` in the
request body. The replacement takes it in the query, beside the read that
always did. So this is the one operation in the swap-places set whose *contract*
moved as well as its address, and it cannot be a re-registration: the current
handler would reject the old body outright, since ``ApprovalModeChoice`` forbids
extra fields.

The old body model is declared here rather than left behind in the current
schemas module. That is the whole point of this package — the retiring contract
lives with the retiring addresses, and deleting one deletes the other.
"""

from __future__ import annotations

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.approvals.router import (
    set_approval_mode,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.approvals.schemas import (
    ApprovalMode,
    ApprovalModeChoice,
    ApprovalState,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
    StageQuery,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.di import Injected

from ._shim import legacy_route, legacy_router


# It keeps the name, and the replacement took `ApprovalModeChoice`. A component
# name is part of what a generated client is written against, so leaving this
# one on the new mode-only shape would strip `session_key` from the type a
# caller on this address still constructs. The name goes when this address does.
#
# The docstring stays plain: it is published as the schema's description.
class ApprovalModeSet(BaseModel):
    """The set-the-mode body as it was: the session named inside it."""

    model_config = ConfigDict(extra="forbid")

    session_key: str = Field(description="Session to change the mode for.")
    mode: ApprovalMode = Field(description="The mode to set.")


router = legacy_router("/openapi/v1/bots/approvals", "approvals")


async def set_approval_mode_legacy(
    bot_id: BotIdPath,
    body: ApprovalModeSet,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[ApprovalState]:
    """Set the approval mode for a session.

    Deprecated: use PUT /openapi/v1/bots/{bot_id}/approvals/mode instead,
    which takes session_key as a query parameter like the matching read.
    """
    return await set_approval_mode(
        bot_id=bot_id,
        body=ApprovalModeChoice(mode=body.mode),
        user_id=user_id,
        owner_id=owner_id,
        request=request,
        session_key=body.session_key,
        stage=stage,
        relay=relay,
    )


legacy_route(
    router,
    "PUT",
    "/{bot_id}/mode",
    set_approval_mode_legacy,
    replaces="/openapi/v1/bots/{bot_id}/approvals/mode",
    response_model=Envelope[ApprovalState],
    operation_name="set_approval_mode",
)

__all__ = ["router"]
