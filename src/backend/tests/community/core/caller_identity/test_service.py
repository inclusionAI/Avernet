from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityStage,
    DraftCallTypeMutationResult,
    McpCallType,
)
from agentclaw.community.core.caller_identity.service import CallerIdentityService


def _bot(*, call_type: str = "owner") -> dict[str, object]:
    return {
        "id": 1,
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "bot_type": "service",
        "status": "ACTIVE",
        "active_engine": "openclaw",
        "entity_id": "entity-1",
        "entity_type": "staff",
        "env": "test",
        "call_type": call_type,
    }


def _service(*, bot: dict[str, object]):
    bot_repository = MagicMock()
    bot_repository.get_by_id.return_value = bot
    bot_repository.get_by_id_and_owner.return_value = bot
    collaborator_repository = MagicMock()
    lock_repository = MagicMock()
    mcp_provider = MagicMock()
    repository = MagicMock()
    mcp_sync_service = AsyncMock()
    service = CallerIdentityService(
        bot_repository=bot_repository,
        collaborator_repository=collaborator_repository,
        lock_repository=lock_repository,
        mcp_provider=mcp_provider,
        repository=repository,
        mcp_sync_service=mcp_sync_service,
    )
    return service, SimpleNamespace(
        bot_repository=bot_repository,
        lock_repository=lock_repository,
        mcp_provider=mcp_provider,
        repository=repository,
        mcp_sync_service=mcp_sync_service,
    )


def test_iam_context_reads_only_bot_aggregate_call_type() -> None:
    service, deps = _service(bot=_bot(call_type="caller"))

    context = service.get_iam_token_context(
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
    )

    assert context.should_exchange_caller_token is True
    assert context.bot_call_type is McpCallType.CALLER
    deps.repository.list_draft_call_types.assert_not_called()
    deps.mcp_provider.collect_bot_active_mcps.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_update_syncs_complete_identity_manifest_to_agent_principal() -> None:
    service, deps = _service(bot=_bot())
    deps.lock_repository.get_by_key.return_value = SimpleNamespace(
        holder_user_id="owner-1",
        id=7,
    )
    deps.mcp_provider.collect_bot_active_mcps.return_value = [
        {"server_code": "calendar", "name": "Calendar"},
        {"server_code": "documents", "name": "Documents"},
    ]
    deps.repository.replace_draft_call_type.return_value = DraftCallTypeMutationResult(
        previous_explicit_call_type=None,
        bot_call_type=McpCallType.CALLER,
        revision=1,
    )
    deps.repository.list_draft_call_types.return_value = {
        "calendar": McpCallType.CALLER,
    }
    deps.mcp_sync_service.sync_mcp_identity_to_agent_principal.return_value = {
        "success": True,
    }

    result = await service.update_mcp_call_type(
        bot_id="bot-1",
        server_code="calendar",
        call_type=McpCallType.CALLER,
        actor_id="owner-1",
        lock_epoch=7,
    )

    assert result.bot_call_type is McpCallType.CALLER
    deps.mcp_sync_service.sync_mcp_identity_to_agent_principal.assert_awaited_once_with(
        user_id="owner-1",
        entity_id="entity-1",
        bot_id="bot-1",
        entity_type="staff",
        engine_type="openclaw",
        active_mcps=[
            {"server_code": "calendar", "name": "Calendar"},
            {"server_code": "documents", "name": "Documents"},
        ],
        identity_modes={"calendar": McpCallType.CALLER},
    )
