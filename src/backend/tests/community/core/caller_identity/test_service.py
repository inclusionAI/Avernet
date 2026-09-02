from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.caller_identity.contracts import (
    CallerCallTypeInvalidError,
    CallerCliNotFoundError,
    CallerIdentityAmbiguousError,
    CallerIdentityIrreversibleError,
    CallerIdentityPermissionError,
    CallerIdentityStage,
    CallerIdentityReadOnlyError,
    CallerLockEpochError,
    CallerCliSyncError,
    CallerMcpNotFoundError,
    CallerMcpSyncError,
    DraftCallTypeMutationResult,
    CliCallTypeMutationResult,
    McpCallType,
)
from agentclaw.community.core.caller_identity import service as caller_identity_service
from agentclaw.community.core.caller_identity.credential import CallerToken
from agentclaw.community.core.caller_identity.contracts import CallerIdentityEngineChangedError, CallerIdentityLockMismatchError
from agentclaw.community.core.caller_identity.service import CallerIdentityService
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.mcp.services.cli_passport_scope import (
    CliPassportScopeReconciler,
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


def _service(
    *,
    bot: dict[str, object],
    cli_scope_reconciler=None,
    repository=None,
    passport_plugin=None,
    without_cli_scope_reconciler: bool = False,
):
    bot_repository = MagicMock()
    bot_repository.get_by_id.return_value = bot
    bot_repository.get_unique_by_id.return_value = bot
    bot_repository.get_by_id_and_owner.return_value = bot
    bot_repository.get_by_id_and_entity.return_value = bot
    collaborator_repository = MagicMock()
    lock_repository = MagicMock()
    lock_repository.get_by_key.return_value = None
    mcp_provider = MagicMock()
    repository = repository or MagicMock()
    mcp_sync_service = AsyncMock()
    passport_plugin = None if without_cli_scope_reconciler else passport_plugin or MagicMock()
    cli_scope_reconciler = (
        None
        if without_cli_scope_reconciler
        else cli_scope_reconciler or MagicMock()
    )
    service = CallerIdentityService(
        bot_repository=bot_repository,
        collaborator_repository=collaborator_repository,
        lock_repository=lock_repository,
        mcp_provider=mcp_provider,
        repository=repository,
        mcp_sync_service=mcp_sync_service,
        passport_plugin=passport_plugin,
        cli_scope_reconciler=cli_scope_reconciler,
    )
    return service, SimpleNamespace(
        bot_repository=bot_repository,
        lock_repository=lock_repository,
        mcp_provider=mcp_provider,
        repository=repository,
        mcp_sync_service=mcp_sync_service,
        passport_plugin=passport_plugin,
        cli_scope_reconciler=cli_scope_reconciler,
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


@pytest.mark.asyncio
async def test_cli_caller_updates_full_scope_and_returns_bot_aggregate() -> None:
    """A successful CLI caller update must expose the resulting Bot aggregate."""
    bot = _bot(call_type="owner")
    service, deps = _service(bot=bot)
    deps.repository.replace_draft_cli_call_type.return_value = CliCallTypeMutationResult(
        previous_explicit_call_type=None,
        revision=1,
        bot_call_type=McpCallType.CALLER,
        caller_config_revision=1,
    )
    deps.cli_scope_reconciler.current_passport_cli_items.return_value = [
        {"cli_code": "dataphin", "identity_mode": "owner"},
        {"cli_code": "deepinsight-cli", "identity_mode": "owner"},
    ]
    deps.mcp_provider.collect_bot_active_mcps.return_value = []

    with patch(
        "agentclaw.community.core.caller_identity.service.logger"
    ) as logger:
        result = await service.update_cli_call_type(
            bot_id="bot-1",
            cli_code="dataphin",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
            entity_id="entity-1",
        )

    assert result.cli_code == "dataphin"
    assert result.call_type is McpCallType.CALLER
    assert result.bot_call_type is McpCallType.CALLER
    deps.repository.replace_draft_call_type.assert_not_called()
    deps.cli_scope_reconciler.reconcile.assert_called_once_with(
        bot=bot, force_update=True,
    )
    logged = " ".join(str(call) for call in logger.method_calls)
    assert "cli_call_type_update_requested" in logged
    assert "cli_call_type_update_succeeded" in logged
    assert "actor_id" in logged
    assert "lock_epoch_supplied" in logged
    assert "duration_ms" in logged


@pytest.mark.asyncio
async def test_cli_caller_preserves_agentpass_only_mcp_caller_identity() -> None:
    """CLI mutation must use the same full AgentPass snapshot as Bootstrap."""
    bot = _bot()
    passport = MagicMock()
    passport.query_agent_passport.return_value = {
        "clis": [
            {"cli_code": "dataphin", "identity_mode": "owner"},
            {"cli_code": "deepinsight-cli", "identity_mode": "owner"},
        ],
        "mcps": [
            {"mcp_code": "agentpass-only", "identity_mode": "caller"},
            {"mcp_code": "local-mcp", "identity_mode": "owner"},
        ],
    }
    repository = MagicMock()
    repository.list_draft_call_types.return_value = {}
    repository.list_draft_cli_call_types.return_value = {"dataphin": McpCallType.CALLER}
    reconciler = CliPassportScopeReconciler(
        passport_plugin=passport,  # type: ignore[arg-type]
        identity_repository=repository,  # type: ignore[arg-type]
    )
    service, deps = _service(
        bot=bot,
        cli_scope_reconciler=reconciler,
        repository=repository,
        passport_plugin=passport,
    )
    repository.replace_draft_cli_call_type.return_value = CliCallTypeMutationResult(
        previous_explicit_call_type=None,
        revision=1,
        bot_call_type=McpCallType.CALLER,
        caller_config_revision=1,
    )

    await service.update_cli_call_type(
        bot_id="bot-1",
        cli_code="dataphin",
        call_type=McpCallType.CALLER,
        actor_id="owner-1",
        entity_id="entity-1",
    )

    assert passport.update_passport.call_args.kwargs["resource_scope"]["mcp_items"] == [
        {"mcp_code": "agentpass-only", "identity_mode": "caller"},
        {"mcp_code": "local-mcp", "identity_mode": "owner"},
    ]
    deps.mcp_provider.collect_bot_active_mcps.assert_called_once()


@pytest.mark.asyncio
async def test_cli_scope_failure_compensates_the_sparse_override() -> None:
    bot = _bot()
    service, deps = _service(bot=bot)
    deps.repository.replace_draft_cli_call_type.return_value = CliCallTypeMutationResult(
        previous_explicit_call_type=None,
        revision=1,
        bot_call_type=McpCallType.CALLER,
        caller_config_revision=1,
    )
    deps.cli_scope_reconciler.current_passport_cli_items.return_value = [
        {"cli_code": "dataphin", "identity_mode": "owner"},
    ]
    deps.cli_scope_reconciler.reconcile.side_effect = RuntimeError(
        "scope-token-secret"
    )
    deps.mcp_provider.collect_bot_active_mcps.return_value = []

    with patch(
        "agentclaw.community.core.caller_identity.service.logger"
    ) as logger:
        with pytest.raises(CallerCliSyncError):
            await service.update_cli_call_type(
                bot_id="bot-1",
                cli_code="dataphin",
                call_type=McpCallType.CALLER,
                actor_id="owner-1",
                entity_id="entity-1",
            )

    deps.repository.compensate_draft_cli_call_type.assert_called_once_with(
        bot_pk=1,
        engine_type="openclaw",
        cli_code="dataphin",
        previous_explicit_call_type=None,
        modifier_id="owner-1",
        expected_revision=1,
        expected_caller_config_revision=1,
        lock_key="bot-1:owner-1",
        lock_holder_user_id="owner-1",
        lock_epoch=None,
        effective_server_codes=set(),
        effective_cli_codes={"dataphin"},
    )
    compensation_log = next(
        call
        for call in logger.warning.call_args_list
        if "cli_call_type_update_compensated" in call.args[0]
    )
    assert "actor_id" in compensation_log.args[0]
    assert "lock_epoch_supplied" in compensation_log.args[0]
    assert "duration_ms" in compensation_log.args[0]
    logged = " ".join(str(call) for call in logger.method_calls)
    assert "scope-token-secret" not in logged


@pytest.mark.asyncio
async def test_cli_scope_query_failure_logs_requested_failed_without_secret() -> None:
    """Passport failures must be diagnosable without logging its credentials."""
    service, deps = _service(bot=_bot())
    deps.cli_scope_reconciler.current_passport_cli_items.side_effect = RuntimeError(
        "passport-token-secret"
    )

    with patch(
        "agentclaw.community.core.caller_identity.service.logger"
    ) as logger:
        with pytest.raises(CallerCliSyncError):
            await service.update_cli_call_type(
                bot_id="bot-1",
                cli_code="dataphin",
                call_type=McpCallType.CALLER,
                actor_id="owner-1",
                entity_id="entity-1",
            )

    logged = " ".join(str(call) for call in logger.method_calls)
    assert "cli_call_type_update_requested" in logged
    assert "cli_call_type_update_failed" in logged
    assert "query_scope" in logged
    assert "actor_id" in logged
    assert "duration_ms" in logged
    assert "passport-token-secret" not in logged


@pytest.mark.asyncio
async def test_cli_update_rejects_code_missing_from_agentpass_snapshot() -> None:
    """A typo must not create an override that AgentPass has never authorized."""
    service, deps = _service(bot=_bot())
    deps.cli_scope_reconciler.current_passport_cli_items.return_value = []

    with pytest.raises(CallerCliNotFoundError):
        await service.update_cli_call_type(
            bot_id="bot-1",
            cli_code="unknown-cli",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
            entity_id="entity-1",
        )

    deps.repository.replace_draft_cli_call_type.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_type", "template_type"),
    [
        ("aicoding", None),
        ("claude_code", "normalCC"),
    ],
)
async def test_cli_update_rejects_profiles_outside_phase_one(
    engine_type: str, template_type: str | None
) -> None:
    """CLI caller overrides are limited to the manifest's phase-one profiles."""
    bot = _bot()
    bot["active_engine"] = engine_type
    bot["template_type"] = template_type
    service, deps = _service(bot=bot)
    deps.cli_scope_reconciler.supports_profile.return_value = False

    with pytest.raises(CallerIdentityReadOnlyError):
        await service.update_cli_call_type(
            bot_id="bot-1",
            cli_code="dataphin",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
            entity_id="entity-1",
        )

    deps.cli_scope_reconciler.current_passport_cli_items.assert_not_called()
    deps.repository.replace_draft_cli_call_type.assert_not_called()


@pytest.mark.asyncio
async def test_cli_update_rejects_when_scope_reconciler_is_unavailable() -> None:
    """A caller override may not be persisted without scope compensation."""
    service, deps = _service(
        bot=_bot(),
        passport_plugin=None,
        without_cli_scope_reconciler=True,
    )

    with pytest.raises(CallerCliSyncError):
        await service.update_cli_call_type(
            bot_id="bot-1",
            cli_code="dataphin",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
            entity_id="entity-1",
        )

    deps.repository.replace_draft_cli_call_type.assert_not_called()


@pytest.mark.asyncio
async def test_cli_update_rejects_mismatched_collaboration_lock() -> None:
    """A concurrent editor's lock must fence an otherwise valid CLI mutation."""
    service, deps = _service(bot=_bot())
    deps.cli_scope_reconciler.current_passport_cli_items.return_value = [
        {"cli_code": "dataphin", "identity_mode": "owner"},
    ]
    deps.lock_repository.get_by_key.return_value = SimpleNamespace(
        holder_user_id="another-owner", id=9
    )

    with pytest.raises(CallerLockEpochError):
        await service.update_cli_call_type(
            bot_id="bot-1",
            cli_code="dataphin",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
            lock_epoch=9,
            entity_id="entity-1",
        )

    deps.repository.replace_draft_cli_call_type.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repository_error", "expected_error"),
    [
        (CallerIdentityLockMismatchError(), CallerLockEpochError),
        (CallerIdentityEngineChangedError(), CallerIdentityReadOnlyError),
    ],
)
async def test_cli_update_maps_persistence_fencing_errors(
    repository_error: Exception, expected_error: type[Exception]
) -> None:
    """The API keeps the established lock/engine error contract for CLI rows."""
    service, deps = _service(bot=_bot())
    deps.cli_scope_reconciler.current_passport_cli_items.return_value = [
        {"cli_code": "dataphin", "identity_mode": "owner"},
    ]
    deps.repository.replace_draft_cli_call_type.side_effect = repository_error

    with pytest.raises(expected_error):
        await service.update_cli_call_type(
            bot_id="bot-1",
            cli_code="dataphin",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
            entity_id="entity-1",
        )


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


def test_exchange_caller_identity_installs_opaque_token_without_passport_lookup() -> None:
    service, _ = _service(bot=_bot(call_type="caller"))
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
        token_provider=token_provider,
        runtime_updater=runtime_updater,
        stage="draft",
        publish_id=None,
        entity_id="entity-1",
        binding_id=9,
    )

    token_provider.exchange.assert_called_once_with(
        auth_context=caller_identity_service.AuthContext(user_id="caller-1"),
        iam_token="iam-token",
        bot_id="bot-1",
        owner_user_id="owner-1",
        task_metadata=caller_identity_service.CALLER_CHAT_TASK,
    )
    runtime_updater.update_caller_identity.assert_called_once_with(
        bot_id="bot-1",
        owner_user_id="owner-1",
        caller_user_id="caller-1",
        caller_token=caller_token,
        stage="draft",
        publish_id=None,
        entity_id="entity-1",
        binding_id=9,
    )


def test_exchange_caller_identity_reuses_supplied_caller_token() -> None:
    service, _ = _service(bot=_bot(call_type="caller"))
    token_provider = MagicMock()
    caller_token = CallerToken(
        access_token="caller-token",
        subject_user_id="caller-1",
        expires_at=datetime.now(),
        fingerprint="fingerprint",
    )
    runtime_updater = MagicMock()

    result = service.exchange_caller_identity(
        iam_token="iam-token",
        caller_user_id="caller-1",
        bot_id="bot-1",
        owner_user_id="owner-1",
        token_provider=token_provider,
        runtime_updater=runtime_updater,
        stage="online",
        publish_id=1,
        entity_id="entity-1",
        binding_id=9,
        caller_token=caller_token,
    )

    assert result is None
    token_provider.exchange.assert_not_called()
    assert runtime_updater.update_caller_identity.call_args.kwargs["caller_token"] is caller_token


def test_exchange_caller_identity_propagates_test_exchange_to_runtime() -> None:
    service, _ = _service(bot=_bot(call_type="owner"))
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
def test_exchange_caller_identity_passes_binding_id_in_all_stages(
    stage: str,
) -> None:
    service, _ = _service(bot=_bot(call_type="caller"))
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
        token_provider=token_provider,
        runtime_updater=runtime_updater,
        stage=stage,
        publish_id=1,
        entity_id="entity-1",
        binding_id=9,
    )

    assert runtime_updater.update_caller_identity.call_args.kwargs["binding_id"] == 9


@pytest.mark.parametrize("binding_id", [None, 0, -1, False])
def test_exchange_caller_identity_does_not_pass_invalid_binding_id(
    binding_id: int | None | bool,
) -> None:
    """Invalid binding_id values should not be passed to runtime_updater."""
    service, _ = _service(bot=_bot(call_type="caller"))
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
        token_provider=token_provider,
        runtime_updater=runtime_updater,
        stage="online",
        publish_id=1,
        entity_id="entity-1",
        binding_id=binding_id,
    )

    # Invalid binding_id should not be in kwargs
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


def test_caller_context_returns_mcp_and_cli_sparse_caller_overrides() -> None:
    """The unified read must expose both resource kinds without an AgentPass call."""
    service, deps = _service(bot=_bot())
    deps.lock_repository.get_by_key.return_value = None
    deps.repository.list_draft_call_types.return_value = {
        "mcp.calendar": McpCallType.CALLER,
    }
    deps.repository.list_draft_cli_call_types.return_value = {
        "dataphin": McpCallType.CALLER,
    }

    context = service.get_context(
        bot_id="bot-1",
        actor_id="owner-1",
        stage=CallerIdentityStage.DRAFT,
    )

    assert context.mcp_call_types == {"mcp.calendar": McpCallType.CALLER}
    assert context.cli_call_types == {"dataphin": McpCallType.CALLER}
    deps.repository.list_draft_call_types.assert_called_once_with(1, "openclaw")
    deps.repository.list_draft_cli_call_types.assert_called_once_with(1, "openclaw")
    deps.cli_scope_reconciler.current_passport_cli_items.assert_not_called()


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


@pytest.mark.asyncio
async def test_mcp_update_rejects_irreversible_owner_downgrade_before_sync() -> None:
    service, deps = _service(bot=_bot(call_type="caller"))
    deps.mcp_provider.collect_bot_active_mcps.return_value = [
        {"server_code": "calendar"}
    ]
    deps.lock_repository.get_by_key.return_value = None
    deps.repository.replace_draft_call_type.side_effect = (
        CallerIdentityIrreversibleError()
    )

    with pytest.raises(CallerIdentityIrreversibleError):
        await service.update_mcp_call_type(
            bot_id="bot-1",
            server_code="calendar",
            call_type=McpCallType.OWNER,
            actor_id="owner-1",
        )

    deps.mcp_sync_service.sync_mcp_identity_to_agent_principal.assert_not_awaited()
    deps.repository.compensate_draft_call_type.assert_not_called()
