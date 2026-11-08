from tests.community.bot_address_fakes import support_legacy_locator

"""Regression tests: skills API endpoints must properly await their async service methods.

These tests FAIL before the fix (proving the bug exists) and PASS after the fix.

Bug summary:
- activate_skill  (line 712): run_in_threadpool(service.activate_skill, ...) -
    starlette's run_in_threadpool does NOT properly schedule an async coroutine
    (it calls iscoroutinefunction check and then just awaits directly, defeating
    the purpose; the real fix is to await service.activate_skill directly).
- deactivate_skill (line 776): service.deactivate_skill(...) is called WITHOUT await,
    returning a coroutine that is never awaited.
- activate_skills_batch (line 1108): same run_in_threadpool bug as activate_skill.

The fix for all three is to `await` the service method directly instead of wrapping
it in run_in_threadpool.
"""



from contextlib import contextmanager

from pathlib import Path

from types import SimpleNamespace

from unittest.mock import AsyncMock, MagicMock

import pytest

from fastapi import FastAPI

from fastapi.testclient import TestClient

from fastapi_injector import attach_injector

from injector import Injector, Module

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)

from agentclaw.community.adapters.http.skill_center.skills import (
    router as skills_router,
)



from agentclaw.community.api.runtime_layout_probe_service import (
    RuntimeLayoutProbeServiceProtocol,
)

from agentclaw.community.api.skill_query_service import SkillQueryServiceProtocol

from agentclaw.community.api.direct_activation_service import (
    DirectActivationServiceProtocol,
)

from agentclaw.community.core.skill_center.errors import (
    LocalSkillRuntimeSyncError,
)

from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    LAYOUT_CONTRACT_VERSION,
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeStatus,
)



@pytest.fixture
def mock_ctx():
    return RequestContext(user_id="user_001", bot_id="default")

@contextmanager
def _skill_service_di_app(
    mock_ctx,
    *,
    device_sync_result=None,
    runtime_uses_pool_paths=False,
    bot_type="personal",
    engine_type="openclaw",
    runtime_probe_result=None,
    management_active_root=Path("/backend/legacy/skills"),
):
    """Build a TestClient whose SkillService has AsyncMock methods.

    Yields (client, mock_skill_service).  The mock_skill_service is the SAME
    object that the router's SkillServiceFactory.create() returns, so assertions
    on it reflect what the endpoint actually called.
    """
    # Service with async methods
    mock_skill_service = MagicMock()
    mock_skill_service.activate_skill = AsyncMock(return_value=True)
    mock_skill_service.deactivate_skill = AsyncMock(return_value=True)
    mock_skill_service.activate_skills_batch = AsyncMock(
        return_value={
            "success": [{"id": "a", "link_name": "a", "path": "git://path/a"}],
            "failed": [],
        }
    )
    mock_skill_service.get_link_name = MagicMock(return_value="link_name")
    mock_skill_service.get_active_skills_from_device = AsyncMock(return_value=[])
    mock_skill_service.runtime_uses_pool_paths = runtime_uses_pool_paths
    mock_skill_service.active_dir = None

    mock_asset_service = MagicMock()

    def _resolve_legacy(**kwargs):
        return {"git://path/a": "1", "git://path/b": "2"}.get(
            kwargs["skill_reference"], "1"
        )

    mock_asset_service.resolve_legacy_skill_id.side_effect = _resolve_legacy
    _command_error = (
        LocalSkillRuntimeSyncError()
        if runtime_uses_pool_paths
        and device_sync_result
        and not device_sync_result.get("success")
        else None
    )
    _command_result = {
        "id": "1",
        "name": "a",
        "link_name": "a",
        "git_path": "git://path/a",
    }
    mock_asset_service.activate_skill = AsyncMock(
        side_effect=_command_error, return_value=_command_result
    )
    mock_asset_service.deactivate_skill = AsyncMock(
        side_effect=_command_error, return_value=_command_result
    )
    mock_skill_service.asset_service = mock_asset_service

    # Factory that returns the mock service from create()
    mock_skill_service_factory = MagicMock()

    def _create_skill_service(**kwargs):
        mock_skill_service.active_dir = kwargs.get("active_dir")
        return mock_skill_service

    mock_skill_service_factory.create.side_effect = _create_skill_service

    # Mock path_factory - all get_* methods return temp Path objects
    mock_path_factory = MagicMock()
    mock_path_factory.get_bot_skills_dir.return_value = management_active_root
    mock_path_factory.get_bot_skills_local_dir.return_value = MagicMock()
    mock_path_factory.get_bot_engine_dir.return_value = MagicMock()
    mock_path_factory.get_bot_skills_repo_dir.return_value = MagicMock()

    # Mock skill_set_service_factory
    mock_skill_set_service = MagicMock()
    mock_skill_set_service.get_symlink_mappings.return_value = []
    mock_skill_set_service_factory = MagicMock()
    mock_skill_set_service_factory.create.return_value = mock_skill_set_service

    # Mock device_sync
    mock_device_sync = MagicMock()
    mock_device_sync.sync_symlinks.return_value = (
        device_sync_result
        if device_sync_result is not None
        else {"success": True, "message": "synced"}
    )

    # Mock bot_repo - runtime-checkable Protocol
    mock_bot_repo = MagicMock()
    support_legacy_locator(mock_bot_repo)
    mock_bot_repo.get_device_provider_by_bot_id_and_owner.return_value = {
        "device_provider": "local"
    }
    mock_bot_repo.get_by_id_and_owner.return_value = {
        "bot_id": "default",
        "owner_id": "user_001",
        "active_engine": engine_type,
        "bot_type": bot_type,
    }
    mock_bot_repo.list_by_owner.return_value = (0, [])

    app = FastAPI()
    app.include_router(skills_router)
    app.dependency_overrides[get_request_context] = lambda: mock_ctx

    # The skills endpoints resolve DeviceContext before dispatching DeviceSync.
    mock_ctx_obj = MagicMock()
    mock_resolver = MagicMock()
    mock_resolver.resolve_for_bot.return_value = mock_ctx_obj
    mock_device_sync_dispatcher = MagicMock()
    mock_device_sync_dispatcher.dispatch.return_value = mock_device_sync

    runtime_active_roots = {
        "openclaw": "/home/admin/.openclaw/workspace/skills",
        "claude_code": "/home/admin/.claude/skills",
        "aicoding": "/home/admin/.claude/skills",
        "hermes": "/home/admin/.hermes/skills",
    }
    if runtime_probe_result is None:
        runtime_probe_result = RuntimeLayoutProbeResult(
            status=RuntimeLayoutProbeStatus.READY,
            engine=engine_type,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            preparation_id="prep-test",
            evidence={
                "resolved_layout": {
                    "active_root": runtime_active_roots.get(
                        engine_type, "/unused/active"
                    )
                }
            },
        )
    mock_runtime_layout_probe = MagicMock()
    mock_runtime_layout_probe.probe_bot = AsyncMock(return_value=runtime_probe_result)
    mock_skill_service.runtime_layout_probe = mock_runtime_layout_probe

    class _TestModule(Module):
        def configure(self, binder):
            from agentclaw.community.core.repository.protocols.bot import BotRepository
            from agentclaw.community.core.devices.services.device_context_resolver import (
                DeviceContextResolver,
            )
            from agentclaw.community.core.skill_center.factories import (
                SkillServiceFactory,
            )
            from agentclaw.community.core.workspace.path_factory import (
                WorkspacePathFactory,
            )
            from agentclaw.community.di.modules.skill_center_module import (
                SkillSetServiceFactory,
            )
            from agentclaw.community.plugin_api.device_sync_dispatcher import (
                DeviceSyncDispatcher,
            )

            from agentclaw.community.api.skill_service_factory import (
                SkillServiceFactoryProtocol,
            )
            from agentclaw.community.api.skill_set_service_factory import (
                SkillSetServiceFactoryProtocol,
            )

            binder.bind(SkillServiceFactory, to=mock_skill_service_factory)
            binder.bind(SkillServiceFactoryProtocol, to=mock_skill_service_factory)
            binder.bind(WorkspacePathFactory, to=mock_path_factory)
            binder.bind(SkillSetServiceFactory, to=mock_skill_set_service_factory)
            binder.bind(
                SkillSetServiceFactoryProtocol, to=mock_skill_set_service_factory
            )
            binder.bind(DeviceContextResolver, to=mock_resolver)
            binder.bind(DeviceSyncDispatcher, to=mock_device_sync_dispatcher)
            binder.bind(BotRepository, to=mock_bot_repo)
            binder.bind(
                RuntimeLayoutProbeServiceProtocol,
                to=mock_runtime_layout_probe,
            )
            binder.bind(SkillQueryServiceProtocol, to=mock_asset_service)
            binder.bind(DirectActivationServiceProtocol, to=mock_asset_service)

    injector = Injector([_TestModule()])
    attach_injector(app, injector)
    client = TestClient(app, raise_server_exceptions=False)
    yield client, mock_skill_service, mock_skill_set_service

@contextmanager
def _upload_skill_di_app(
    mock_ctx,
    bot_status="ACTIVE",
    bot_type="personal",
    bot_service=None,
    device_id=None,
    bot_owner_id=None,
    engine_type="openclaw",
):
    bot_owner_id = bot_owner_id or mock_ctx.user_id
    mock_skill_service = MagicMock()
    mock_skill_service.upload_skill = AsyncMock(
        return_value={
            "id": "1",
            "name": "uploaded-skill",
            "description": "uploaded",
            "git_path": "local:///tmp/skills-local/uploaded-skill",
            "link_name": None,
            "category": "general",
            "tags": "[]",
            "risk_tags": [],
            "mcp_dependencies": [],
            "input_schema": "",
            "output_schema": "",
            "is_public": False,
            "is_builtin": False,
            "user_id": bot_owner_id,
            "bot_id": mock_ctx.bot_id,
            "bolt_id": mock_ctx.bot_id,
            "gmt_created": "",
            "gmt_modified": "",
        }
    )

    mock_skill_service_factory = MagicMock()
    mock_skill_service_factory.create.return_value = mock_skill_service

    async def upload_local_skill_files(*, bot_id, owner_id, actor_id, files):
        uploaded_files = [
            {
                "filename": Path(relative_path).name,
                "relative_path": relative_path,
                "content": content,
            }
            for relative_path, content in files
        ]
        skill = await mock_skill_service.upload_skill(
            uploaded_files,
            user_id=owner_id,
            bolt_id=bot_id,
        )
        return {"operation": "created", "skill": skill, "actor_id": actor_id}

    mock_skill_service.upload_local_skill_files = AsyncMock(
        side_effect=upload_local_skill_files
    )

    mock_path_factory = MagicMock()
    mock_path_factory.get_bot_skills_dir.return_value = MagicMock()
    mock_path_factory.get_bot_skills_local_dir.return_value = MagicMock()
    mock_path_factory.get_bot_engine_dir.return_value = MagicMock()
    mock_path_factory.get_bot_skills_repo_dir.return_value = MagicMock()

    bot_record = {
        "bot_id": mock_ctx.bot_id,
        "owner_id": bot_owner_id,
        "entity_id": f"staff_{bot_owner_id}",
        "env": "local",
        "active_engine": engine_type,
        "bot_type": bot_type,
        "status": bot_status,
    }
    if device_id is not None:
        bot_record["device_id"] = device_id

    mock_bot_repo = MagicMock()
    support_legacy_locator(mock_bot_repo)
    mock_bot_repo.get_by_id_and_owner.return_value = bot_record

    # upload_skill now resolves the device provider (teclaw branch); a non-teclaw
    # resolver keeps these validation tests on the unchanged path.
    mock_resolver = MagicMock()
    mock_resolver.resolve_for_bot.return_value = MagicMock(provider="arca")

    # BotService is required only when the gate consults BaaS live status
    # (desktop bots). Callers that don't pass one get a MagicMock whose
    # resolve_desktop_live_status returns None → DB-only behavior.
    if bot_service is None:
        bot_service = MagicMock()
        bot_service.resolve_desktop_live_status = MagicMock(return_value=None)

    mock_edit_guard = MagicMock()
    mock_edit_guard.acquire_for_edit.return_value = object()
    mock_lock_service = MagicMock()
    mock_lock_service.get_lock_info.return_value = SimpleNamespace(
        has_collaborators=True,
        lock=SimpleNamespace(holder_user_id=mock_ctx.user_id),
        holder_name="collaborator",
    )
    mock_collaborator_service = MagicMock()
    mock_collaborator_service.check_collaborator_permission.return_value = {
        "has_permission": True,
        "level": "ADMIN",
    }

    app = FastAPI()
    app.include_router(skills_router)
    app.dependency_overrides[get_request_context] = lambda: mock_ctx

    class _TestModule(Module):
        def configure(self, binder):
            from agentclaw.community.api.bot_service import BotServiceProtocol
            from agentclaw.community.core.repository.protocols.bot import BotRepository
            from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
                CollaboratorLockService,
            )
            from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
                CollaboratorService,
            )
            from agentclaw.community.core.devices.services.device_context_resolver import (
                DeviceContextResolver,
            )
            from agentclaw.community.core.skill_center.factories import (
                SkillServiceFactory,
            )
            from agentclaw.community.core.skills_pool.edit_guard import (
                SkillsPoolEditGuard,
            )
            from agentclaw.community.core.workspace.path_factory import (
                WorkspacePathFactory,
            )

            from agentclaw.community.api.skill_service_factory import (
                SkillServiceFactoryProtocol,
            )
            from agentclaw.community.api.local_skill_upload_service import (
                LocalSkillUploadServiceProtocol,
            )

            binder.bind(SkillServiceFactory, to=mock_skill_service_factory)
            binder.bind(SkillServiceFactoryProtocol, to=mock_skill_service_factory)
            binder.bind(LocalSkillUploadServiceProtocol, to=mock_skill_service)
            binder.bind(WorkspacePathFactory, to=mock_path_factory)
            binder.bind(BotRepository, to=mock_bot_repo)
            binder.bind(DeviceContextResolver, to=mock_resolver)
            binder.bind(BotServiceProtocol, to=bot_service)
            binder.bind(SkillsPoolEditGuard, to=mock_edit_guard)
            binder.bind(CollaboratorLockService, to=mock_lock_service)
            binder.bind(CollaboratorService, to=mock_collaborator_service)

    injector = Injector([_TestModule()])
    attach_injector(app, injector)
    client = TestClient(app, raise_server_exceptions=False)
    yield client, mock_skill_service, mock_bot_repo, mock_skill_service_factory

