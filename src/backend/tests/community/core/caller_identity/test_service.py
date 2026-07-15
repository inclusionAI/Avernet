from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.caller_identity.contracts import (
    CallerCallTypeInvalidError,
    CallerIdentityStage,
    CallerIdentityReadOnlyError,
    CallerLockEpochError,
    CallerMcpNotFoundError,
    CallerMcpSyncError,
    DraftCallTypeMutationResult,
    McpCallType,
)
from agentclaw.community.core.caller_identity.repository import (
    CallerIdentityEngineChangedError,
    CallerIdentityLockMismatchError,
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active_mcps", "lock", "expected_error"),
    [
        ([], None, CallerMcpNotFoundError),
        (
            [{"server_code": "calendar"}],
            SimpleNamespace(holder_user_id="other-user", id=7),
            CallerLockEpochError,
        ),
    ],
)
async def test_mcp_update_rejects_missing_mcp_and_stale_lock(
    active_mcps: list[dict[str, str]],
    lock: SimpleNamespace | None,
    expected_error: type[Exception],
) -> None:
    service, deps = _service(bot=_bot())
    deps.mcp_provider.collect_bot_active_mcps.return_value = active_mcps
    deps.lock_repository.get_by_key.return_value = lock

    with pytest.raises(expected_error):
        await service.update_mcp_call_type(
            bot_id="bot-1",
            server_code="calendar",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
            lock_epoch=7,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repository_error", "expected_error"),
    [
        (CallerIdentityLockMismatchError(), CallerLockEpochError),
        (CallerIdentityEngineChangedError(), CallerIdentityReadOnlyError),
        (ValueError("invalid call type"), CallerCallTypeInvalidError),
    ],
)
async def test_mcp_update_maps_repository_concurrency_errors(
    repository_error: Exception,
    expected_error: type[Exception],
) -> None:
    service, deps = _service(bot=_bot())
    deps.mcp_provider.collect_bot_active_mcps.return_value = [
        {"server_code": "calendar"}
    ]
    deps.lock_repository.get_by_key.return_value = SimpleNamespace(
        holder_user_id="owner-1",
        id=7,
    )
    deps.repository.replace_draft_call_type.side_effect = repository_error

    with pytest.raises(expected_error):
        await service.update_mcp_call_type(
            bot_id="bot-1",
            server_code="calendar",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
            lock_epoch=7,
        )


@pytest.mark.asyncio
async def test_mcp_update_compensates_when_agent_principal_sync_fails() -> None:
    service, deps = _service(bot=_bot())
    deps.mcp_provider.collect_bot_active_mcps.return_value = [
        {"server_code": "calendar"}
    ]
    deps.lock_repository.get_by_key.return_value = SimpleNamespace(
        holder_user_id="owner-1",
        id=7,
    )
    deps.repository.replace_draft_call_type.return_value = DraftCallTypeMutationResult(
        previous_explicit_call_type=None,
        bot_call_type=McpCallType.CALLER,
        revision=3,
    )
    deps.repository.list_draft_call_types.return_value = {
        "calendar": McpCallType.CALLER,
    }
    deps.mcp_sync_service.sync_mcp_identity_to_agent_principal.side_effect = (
        RuntimeError("Agent Principal unavailable")
    )
    deps.repository.compensate_draft_call_type.return_value = SimpleNamespace(
        applied=True,
        revision=4,
    )

    with pytest.raises(CallerMcpSyncError):
        await service.update_mcp_call_type(
            bot_id="bot-1",
            server_code="calendar",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
            lock_epoch=7,
        )

    assert (
        deps.repository.compensate_draft_call_type.call_args.kwargs["expected_revision"]
        == 3
    )


@pytest.mark.asyncio
async def test_mcp_update_keeps_sync_error_when_compensation_fails() -> None:
    service, deps = _service(bot=_bot())
    deps.mcp_provider.collect_bot_active_mcps.return_value = [
        {"server_code": "calendar"}
    ]
    deps.lock_repository.get_by_key.return_value = SimpleNamespace(
        holder_user_id="owner-1",
        id=7,
    )
    deps.repository.replace_draft_call_type.return_value = DraftCallTypeMutationResult(
        previous_explicit_call_type=None,
        bot_call_type=McpCallType.CALLER,
        revision=3,
    )
    deps.repository.list_draft_call_types.return_value = {
        "calendar": McpCallType.CALLER,
    }
    deps.mcp_sync_service.sync_mcp_identity_to_agent_principal.side_effect = (
        RuntimeError("Agent Principal unavailable")
    )
    deps.repository.compensate_draft_call_type.side_effect = RuntimeError(
        "compensation unavailable"
    )

    with pytest.raises(CallerMcpSyncError):
        await service.update_mcp_call_type(
            bot_id="bot-1",
            server_code="calendar",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
            lock_epoch=7,
        )

    deps.repository.compensate_draft_call_type.assert_called_once()


def test_bot_call_type_accessors_use_the_aggregate_bot_value() -> None:
    service, _ = _service(bot=_bot(call_type="caller"))

    assert (
        service.get_bot_call_type(
            "bot-1",
            CallerIdentityStage.ONLINE,
            publish_id=1,
        )
        is McpCallType.CALLER
    )
    assert service.is_caller_bot("bot-1", CallerIdentityStage.DRAFT) is True


def test_bot_call_type_rejects_corrupt_aggregate_value() -> None:
    service, _ = _service(bot=_bot(call_type="unsupported"))

    with pytest.raises(CallerCallTypeInvalidError):
        service.get_bot_call_type("bot-1", CallerIdentityStage.DRAFT)


@pytest.mark.asyncio
async def test_mcp_update_rejects_invalid_call_type_before_mutation() -> None:
    service, deps = _service(bot=_bot())

    with pytest.raises(CallerCallTypeInvalidError):
        await service.update_mcp_call_type(
            bot_id="bot-1",
            server_code="calendar",
            call_type="unsupported",  # type: ignore[arg-type]
            actor_id="owner-1",
            lock_epoch=7,
        )

    deps.bot_repository.get_by_id_and_owner.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_update_rejects_non_service_bot_as_read_only() -> None:
    bot = _bot()
    bot["bot_type"] = "personal"
    service, deps = _service(bot=bot)

    with pytest.raises(CallerIdentityReadOnlyError):
        await service.update_mcp_call_type(
            bot_id="bot-1",
            server_code="calendar",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
            lock_epoch=7,
        )

    deps.mcp_provider.collect_bot_active_mcps.assert_not_called()
