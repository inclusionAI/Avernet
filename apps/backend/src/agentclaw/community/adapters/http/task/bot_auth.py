"""Gateway-authenticated Bot admission for Relay's node-scoped HTTP surface."""

from __future__ import annotations

from dataclasses import replace
import hmac

from fastapi import HTTPException, Request
from fastapi_injector import get_injector_instance

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.gateway_principal import (
    BotPrincipal,
    PrincipalVerificationError,
    verify_task_bot_principal_token,
)
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import TaskNode
from agentclaw.community.core.task.task_runner.client.bcs_bot_identity_resolver import (
    BotServiceBcsBotIdentityResolver,
)
from agentclaw.community.utils.gateway_principal_config import (
    ORDINARY_HTTP_ISSUERS,
    get_principal_verifier_config,
)


async def require_task_bot(request: Request) -> BotPrincipal:
    """Never trust an asserted bot ID or the legacy no-op callback authenticator."""
    config = replace(
        get_principal_verifier_config(),
        issuer=ORDINARY_HTTP_ISSUERS,
        verify_audience=False,
    )
    try:
        caller = verify_task_bot_principal_token(
            request.headers.get(PRINCIPAL_HEADER, "").strip(), config,
        )
    except PrincipalVerificationError:
        raise HTTPException(status_code=401, detail="Bot authentication required") from None
    principal = caller.principals[0]
    assert isinstance(principal, BotPrincipal)
    return principal


def require_node_assignee(node: TaskNode, bot: BotPrincipal, request: Request, graph) -> None:
    """A Bot may operate only on the baton assigned to its BCS identity."""
    assignee = str(node.run_info.assignee or "").strip()
    if not assignee:
        raise TaskStateError("relay node has no assigned Bot")
    expected = node.run_info.extend_props.get("assignee_bcs_bot_uuid")
    if expected is None:
        if ":" in assignee:
            expected = assignee
        elif node.node_id == graph.task_id:
            owner_id = str(graph.extend_props.get("owner_user_id") or "")
            if not owner_id:
                raise TaskStateError("relay root owner is unavailable")
            expected = f"{assignee}:{owner_id}"
        else:
            try:
                bot_service = get_injector_instance(request.app).get(BotServiceProtocol)
            except Exception as exc:
                raise TaskStateError("Bot identity resolver is unavailable") from exc
            expected = BotServiceBcsBotIdentityResolver(bot_service).resolve_many([assignee])[assignee]
    assignee = str(expected)
    if not assignee or assignee != bot.bot.bot_uuid:
        raise TaskStateError("Bot is not the assigned relay executor")


def require_relay_attempt(node: TaskNode, relay_token: str) -> None:
    """Compare the delivered run generation; it conveys no authorization itself."""
    current = str(node.run_info.extend_props.get("relay_token") or "")
    if not current or not relay_token or not hmac.compare_digest(current, relay_token):
        raise TaskStateError("relay execution attempt has expired")
