from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.caller_identity.contracts import (
    CallerCallTypeInvalidError,
    CallerIdentityAmbiguousError,
    CallerIdentityPermissionError,
    CallerIdentityStage,
    CallerIdentityReadOnlyError,
    CallerLockEpochError,
    CallerMcpNotFoundError,
    CallerMcpSyncError,
    DraftCallTypeMutationResult,
    McpCallType,
)
from agentclaw.community.core.caller_identity import service as caller_identity_service
from agentclaw.community.core.caller_identity.credential import CallerToken
from agentclaw.community.core.caller_identity.repository import (
    CallerIdentityEngineChangedError,
    CallerIdentityLockMismatchError,
)
from agentclaw.community.core.caller_identity.service import CallerIdentityService
from agentclaw.community.core.bot_management.repository.protocol import (
    BotLookupAmbiguousError,
)


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
    bot_repository.get_unique_by_id.return_value = bot
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
    bot = _bot(call_type="caller")
    bot["binding_id"] = 9
    service, deps = _service(bot=bot)

    context = service.get_iam_token_context(
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
    )

    assert context.should_exchange_caller_token is True
    assert context.bot_call_type == McpCallType.CALLER
    assert context.binding_id == 9
    deps.repository.list_draft_call_types.assert_not_called()
    deps.mcp_provider.collect_bot_active_mcps.assert_not_called()


def test_iam_context_should_not_exchange_when_call_type_not_caller() -> None:
    """Verify bot_call_type uses == comparison, not is."""
    bot = _bot(call_type="owner")
    service, deps = _service(bot=bot)

    context = service.get_iam_token_context(
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
    )

    assert context.bot_call_type == McpCallType.OWNER
    deps.repository.list_draft_call_types.assert_not_called()
    deps.mcp_provider.collect_bot_active_mcps.assert_not_called()


def test_iam_context_test_exchange_skips_bot_type_and_call_type() -> None:
    bot = _bot(call_type="owner")
    bot["bot_type"] = "personal"
    bot["call_type"] = "invalid-for-test"
    service, _ = _service(bot=bot)

    context = service.get_iam_token_context(
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        is_test_exchange=True,
    )

    assert context.should_exchange_caller_token is True
    assert context.bot_call_type is McpCallType.OWNER


def test_iam_context_test_exchange_rejects_production_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(caller_identity_service, "get_current_env", lambda: "prod")
    service, deps = _service(bot=_bot(call_type="owner"))

    with pytest.raises(CallerIdentityPermissionError):
        service.get_iam_token_context(
            bot_id="bot-1",
            stage=CallerIdentityStage.DRAFT,
            is_test_exchange=True,
        )

    deps.bot_repository.get_by_id.assert_not_called()


def test_test_exchange_authorization_rejects_non_owner() -> None:
    service, _ = _service(bot=_bot(call_type="owner"))

    with pytest.raises(CallerIdentityPermissionError):
        service.authorize_iam_token_exchange(
            caller_user_id="caller-1",
            owner_user_id="owner-1",
            is_test_exchange=True,
        )


def test_test_exchange_authorization_allows_owner() -> None:
    service, _ = _service(bot=_bot(call_type="owner"))

    service.authorize_iam_token_exchange(
        caller_user_id="owner-1",
        owner_user_id="owner-1",
        is_test_exchange=True,
    )


def test_iam_context_default_keeps_non_caller_bot_fast_path() -> None:
    bot = _bot(call_type="owner")
    bot["bot_type"] = "personal"
    service, _ = _service(bot=bot)

    context = service.get_iam_token_context(
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
    )

    assert context.should_exchange_caller_token is False


def test_iam_context_test_exchange_still_requires_active_bot() -> None:
    bot = _bot(call_type="owner")
    bot["status"] = "INACTIVE"
    service, _ = _service(bot=bot)

    context = service.get_iam_token_context(
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        is_test_exchange=True,
    )

    assert context.should_exchange_caller_token is False


def test_iam_context_uses_exact_entity_scoped_bot_lookup() -> None:
    service, deps = _service(bot=_bot(call_type="caller"))
    deps.bot_repository.get_by_id_and_entity.return_value = _bot(call_type="caller")

    context = service.get_iam_token_context(
        bot_id="default",
        stage=CallerIdentityStage.DRAFT,
        entity_id="entity-1",
    )

    assert context.should_exchange_caller_token is True
    deps.bot_repository.get_by_id_and_entity.assert_called_once_with(
        "default", "entity-1"
    )
    deps.bot_repository.get_by_id.assert_not_called()


def test_caller_reads_reject_ambiguous_bot_id_without_entity_id() -> None:
    service, deps = _service(bot=_bot(call_type="caller"))
    deps.bot_repository.get_unique_by_id.side_effect = BotLookupAmbiguousError

    with pytest.raises(CallerIdentityAmbiguousError):
        service.get_context(
            bot_id="default",
            actor_id="owner-1",
            stage=CallerIdentityStage.DRAFT,
        )
    with pytest.raises(CallerIdentityAmbiguousError):
        service.get_iam_token_context(
            bot_id="default",
            stage=CallerIdentityStage.DRAFT,
        )
    with pytest.raises(CallerIdentityAmbiguousError):
        service.get_bot_call_type("default", CallerIdentityStage.DRAFT)

    deps.bot_repository.get_by_id.assert_not_called()


@pytest.mark.parametrize(
    "stage",
    [CallerIdentityStage.VERIFY, CallerIdentityStage.ONLINE],
)
def test_iam_context_does_not_reuse_draft_binding_outside_draft(
    stage: CallerIdentityStage,
) -> None:
    bot = _bot(call_type="caller")
    bot["binding_id"] = 9
    service, _ = _service(bot=bot)

    context = service.get_iam_token_context(
        bot_id="bot-1",
        stage=stage,
        publish_id=1,
    )

    assert context.binding_id is None


def test_exchange_caller_identity_uses_passport_and_installs_opaque_token() -> None:
    service, _ = _service(bot=_bot(call_type="caller"))
    passport = MagicMock()
    passport.query_token.return_value = "agent-pass-token"
    passport.query_agent_passport.return_value = {"agent_code": "agent-code"}
    token_provider = MagicMock()
    caller_token = CallerToken(
        access_token="caller-token",
        subject_user_id="caller-1",
        expires_at=datetime.now(),
        fingerprint="fingerprint",
    )
    token_provider.exchange.return_value = caller_token
    runtime_updater = MagicMock()

    service.exchange_caller_identity(
        iam_token="iam-token",
        caller_user_id="caller-1",
        bot_id="bot-1",
        owner_user_id="owner-1",
        passport=passport,
        token_provider=token_provider,
        runtime_updater=runtime_updater,
        stage="draft",
        publish_id=None,
        entity_id="entity-1",
        binding_id=9,
    )

    token_provider.exchange.assert_called_once()
    runtime_updater.update_caller_identity.assert_called_once_with(
        bot_id="bot-1",
        owner_user_id="owner-1",
        caller_user_id="caller-1",
        caller_token=caller_token,
        agent_pass_token="agent-pass-token",
        agent_code="agent-code",
        stage="draft",
        publish_id=None,
        entity_id="entity-1",
        binding_id=9,
    )


def test_exchange_caller_identity_propagates_test_exchange_to_runtime() -> None:
    service, _ = _service(bot=_bot(call_type="owner"))
    passport = MagicMock()
    passport.query_token.return_value = "agent-pass-token"
    passport.query_agent_passport.return_value = {"agent_code": "agent-code"}
    token_provider = MagicMock()
    caller_token = CallerToken(
        access_token="caller-token",
        subject_user_id="owner-1",
        expires_at=datetime.now(),
        fingerprint="fingerprint",
    )
    token_provider.exchange.return_value = caller_token
    runtime_updater = MagicMock()

    service.exchange_caller_identity(
        iam_token="iam-token",
        caller_user_id="owner-1",
        bot_id="bot-1",
        owner_user_id="owner-1",
        passport=passport,
        token_provider=token_provider,
        runtime_updater=runtime_updater,
        stage="draft",
        publish_id=None,
        is_test_exchange=True,
    )

    assert (
        runtime_updater.update_caller_identity.call_args.kwargs["is_test_exchange"]
        is True
    )


@pytest.mark.parametrize("stage", ["verify", "online"])
def test_exchange_caller_identity_does_not_pass_draft_binding_outside_draft(
    stage: str,
) -> None:
    service, _ = _service(bot=_bot(call_type="caller"))
    passport = MagicMock()
    passport.query_token.return_value = "agent-pass-token"
    passport.query_agent_passport.return_value = {"agent_code": "agent-code"}
    token_provider = MagicMock()
    token_provider.exchange.return_value = CallerToken(
        access_token="caller-token",
        subject_user_id="caller-1",
        expires_at=datetime.now(),
        fingerprint="fingerprint",
    )
    runtime_updater = MagicMock()

    service.exchange_caller_identity(
        iam_token="iam-token",
        caller_user_id="caller-1",
        bot_id="bot-1",
        owner_user_id="owner-1",
        passport=passport,
        token_provider=token_provider,
        runtime_updater=runtime_updater,
        stage=stage,
        publish_id=1,
        entity_id="entity-1",
        binding_id=9,
    )

    assert "binding_id" not in runtime_updater.update_caller_identity.call_args.kwargs


@pytest.mark.asyncio
async def test_mcp_update_syncs_complete_identity_manifest_to_agent_principal() -> None:
    service, deps = _service(bot=_bot())
    deps.bot_repository.get_by_id_and_entity.return_value = _bot()
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
        entity_id="entity-1",
    )

    assert result.bot_call_type == McpCallType.CALLER
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
    deps.bot_repository.get_by_id_and_entity.assert_called_once_with(
        "bot-1", "entity-1"
    )


@pytest.mark.asyncio
async def test_mcp_update_without_lock_epoch_allows_unlocked_owner() -> None:
    service, deps = _service(bot=_bot())
    deps.mcp_provider.collect_bot_active_mcps.return_value = [
        {"server_code": "calendar"}
    ]
    deps.lock_repository.get_by_key.return_value = None
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
    )

    assert result.bot_call_type is McpCallType.CALLER
    assert deps.repository.replace_draft_call_type.call_args.kwargs["lock_epoch"] is None


@pytest.mark.asyncio
async def test_mcp_update_without_lock_epoch_rejects_existing_lock() -> None:
    service, deps = _service(bot=_bot())
    deps.mcp_provider.collect_bot_active_mcps.return_value = [
        {"server_code": "calendar"}
    ]
    deps.lock_repository.get_by_key.return_value = SimpleNamespace(
        holder_user_id="owner-1",
        id=7,
    )

    with pytest.raises(CallerLockEpochError):
        await service.update_mcp_call_type(
            bot_id="bot-1",
            server_code="calendar",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
        )

    deps.repository.replace_draft_call_type.assert_not_called()


def test_caller_context_is_editable_for_unlocked_owner() -> None:
    service, deps = _service(bot=_bot())
    deps.lock_repository.get_by_key.return_value = None

    context = service.get_context(
        bot_id="bot-1",
        actor_id="owner-1",
        stage=CallerIdentityStage.DRAFT,
    )

    assert context.editable is True


@pytest.mark.asyncio
async def test_mcp_update_rejects_ambiguous_bot_id_without_entity_id() -> None:
    service, deps = _service(bot=_bot())
    deps.bot_repository.get_unique_by_id.side_effect = BotLookupAmbiguousError

    with pytest.raises(CallerIdentityAmbiguousError):
        await service.update_mcp_call_type(
            bot_id="default",
            server_code="calendar",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
            lock_epoch=7,
        )

    deps.bot_repository.get_by_id_and_owner.assert_not_called()
    deps.mcp_provider.collect_bot_active_mcps.assert_not_called()


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
        (TypeError("transactional session is unavailable"), TypeError),
        (ValueError("persisted call type is corrupt"), ValueError),
    ],
)
async def test_mcp_update_only_maps_repository_domain_errors(
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
async def test_mcp_update_without_epoch_stops_compensation_when_lock_appears() -> None:
    service, deps = _service(bot=_bot())
    deps.mcp_provider.collect_bot_active_mcps.return_value = [
        {"server_code": "calendar"}
    ]
    deps.lock_repository.get_by_key.return_value = None
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
    deps.repository.compensate_draft_call_type.side_effect = (
        CallerIdentityLockMismatchError
    )

    with pytest.raises(CallerMcpSyncError):
        await service.update_mcp_call_type(
            bot_id="bot-1",
            server_code="calendar",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
        )

    assert deps.repository.compensate_draft_call_type.call_args.kwargs["lock_epoch"] is None


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
