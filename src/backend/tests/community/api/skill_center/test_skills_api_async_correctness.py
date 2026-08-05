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
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.adapters.http.skill_center.skills import router as skills_router


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_ctx():
    return RequestContext(user_id="user_001", bot_id="default")


@contextmanager
def _skill_service_di_app(
    mock_ctx,
    *,
    device_sync_result=None,
    runtime_uses_pool_paths=False,
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
        return_value={"success": ["a"], "failed": []}
    )
    mock_skill_service.get_link_name = MagicMock(return_value="link_name")
    mock_skill_service.runtime_uses_pool_paths = runtime_uses_pool_paths

    # Factory that returns the mock service from create()
    mock_skill_service_factory = MagicMock()
    mock_skill_service_factory.create.return_value = mock_skill_service

    # Mock path_factory - all get_* methods return temp Path objects
    mock_path_factory = MagicMock()
    mock_path_factory.get_bot_skills_dir.return_value = MagicMock()
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
    mock_bot_repo.get_device_provider_by_bot_id_and_owner.return_value = {
        "device_provider": "local"
    }
    mock_bot_repo.get_by_id_and_owner.return_value = {
        "bot_id": "default",
        "owner_id": "user_001",
        "active_engine": "openclaw",
    }
    mock_bot_repo.list_by_owner.return_value = (0, [])

    app = FastAPI()
    app.include_router(skills_router)
    app.dependency_overrides[get_request_context] = lambda: mock_ctx

    # New (Task 3): the skills endpoints now depend on
    # DeviceContextResolver + DeviceSyncDispatcher (not the old supplier).
    mock_ctx_obj = MagicMock()
    mock_resolver = MagicMock()
    mock_resolver.resolve_for_bot.return_value = mock_ctx_obj
    mock_device_sync_dispatcher = MagicMock()
    mock_device_sync_dispatcher.dispatch.return_value = mock_device_sync

    class _TestModule(Module):
        def configure(self, binder):
            from agentclaw.community.core.bot_management.repository.protocol import BotRepository
            from agentclaw.community.core.devices.services.device_context_resolver import (
                DeviceContextResolver,
            )
            from agentclaw.community.core.skill_center.factories import SkillServiceFactory
            from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
            from agentclaw.community.di.modules.skill_center_module import (
                SkillSetServiceFactory,
            )
            from agentclaw.community.core.devices.services.device_sync_dispatcher import DeviceSyncDispatcher

            from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
            from agentclaw.community.api.skill_set_service_factory import SkillSetServiceFactoryProtocol
            binder.bind(SkillServiceFactory, to=mock_skill_service_factory)
            binder.bind(SkillServiceFactoryProtocol, to=mock_skill_service_factory)
            binder.bind(WorkspacePathFactory, to=mock_path_factory)
            binder.bind(SkillSetServiceFactory, to=mock_skill_set_service_factory)
            binder.bind(SkillSetServiceFactoryProtocol, to=mock_skill_set_service_factory)
            binder.bind(DeviceContextResolver, to=mock_resolver)
            binder.bind(DeviceSyncDispatcher, to=mock_device_sync_dispatcher)
            binder.bind(BotRepository, to=mock_bot_repo)

    injector = Injector([_TestModule()])
    attach_injector(app, injector)
    client = TestClient(app, raise_server_exceptions=False)
    yield client, mock_skill_service


@contextmanager
def _upload_skill_di_app(
    mock_ctx,
    bot_status="ACTIVE",
    bot_type="personal",
    bot_service=None,
    device_id=None,
    bot_owner_id=None,
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
        "active_engine": "openclaw",
        "bot_type": bot_type,
        "status": bot_status,
    }
    if device_id is not None:
        bot_record["device_id"] = device_id

    mock_bot_repo = MagicMock()
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
            from agentclaw.community.core.bot_management.repository.protocol import BotRepository
            from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
                CollaboratorLockService,
            )
            from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
                CollaboratorService,
            )
            from agentclaw.community.core.devices.services.device_context_resolver import (
                DeviceContextResolver,
            )
            from agentclaw.community.core.skill_center.factories import SkillServiceFactory
            from agentclaw.community.core.skills_pool.edit_guard import (
                SkillsPoolEditGuard,
            )
            from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory

            from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
            binder.bind(SkillServiceFactory, to=mock_skill_service_factory)
            binder.bind(SkillServiceFactoryProtocol, to=mock_skill_service_factory)
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


# ── activate_skill ────────────────────────────────────────────────────────────


class TestUploadSkillValidation:
    def test_upload_rejects_non_active_bot(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="PENDING") as (client, mock_svc, _, _):
            response = client.post(
                "/api/skills/upload",
                files=[
                    ("files", ("SKILL.md", b"---\nname: a\ndescription: a\n---", "text/markdown"))
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            assert response.status_code == 200
            body = response.json()
            assert body["success"] is False
            assert "expected ACTIVE" in body["message"]
            mock_svc.upload_skill.assert_not_called()

    def test_upload_rejects_missing_bot(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (client, mock_svc, mock_bot_repo, _):
            mock_bot_repo.get_by_id_and_owner.return_value = None

            response = client.post(
                "/api/skills/upload",
                files=[
                    ("files", ("SKILL.md", b"---\nname: a\ndescription: a\n---", "text/markdown"))
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert body["success"] is False
            assert body["message"] == "Bot not found."
            mock_svc.upload_skill.assert_not_called()

    def test_upload_rejects_bot_without_owner_metadata(self, mock_ctx):
        """Local Skill 的归属无法确定时，不能将上传归到请求方。"""
        with _upload_skill_di_app(
            mock_ctx, bot_status="ACTIVE"
        ) as (client, mock_svc, mock_bot_repo, _):
            mock_bot_repo.get_by_id_and_owner.return_value["owner_id"] = ""

            response = client.post(
                "/api/skills/upload",
                files=[
                    ("files", ("SKILL.md", b"---\nname: a\ndescription: a\n---", "text/markdown"))
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert body["success"] is False
            assert body["message"] == "Bot ownership metadata is incomplete."
            mock_svc.upload_skill.assert_not_called()

    def test_upload_rejects_file_paths_length_mismatch(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (client, mock_svc, _, _):
            response = client.post(
                "/api/skills/upload",
                files=[
                    ("files", ("SKILL.md", b"---\nname: a\ndescription: a\n---", "text/markdown")),
                    ("files", ("a.txt", b"a", "text/plain")),
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert body["success"] is False
            assert body["message"] == "file_paths length must match files length."
            mock_svc.upload_skill.assert_not_called()

    def test_upload_active_bot_calls_service(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (client, mock_svc, _, _):
            response = client.post(
                "/api/skills/upload",
                files=[
                    ("files", ("SKILL.md", b"---\nname: a\ndescription: a\n---", "text/markdown"))
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert body["success"] is True
            assert mock_svc.upload_skill.await_count == 1

    def test_upload_persists_bot_owner_for_collaborator_upload(self):
        collaborator_ctx = RequestContext(
            user_id="collaborator-9",
            bot_id="service-bot-1",
        )
        with _upload_skill_di_app(
            collaborator_ctx,
            bot_status="ACTIVE",
            bot_type="service",
            bot_owner_id="bot-owner-1",
        ) as (client, mock_svc, _, _):
            response = client.post(
                (
                    "/api/skills/upload?user_id=bot-owner-1"
                    "&bot_id=service-bot-1"
                ),
                files=[
                    ("files", ("SKILL.md", b"---\nname: a\ndescription: a\n---", "text/markdown"))
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            assert response.json()["success"] is True, response.json()
            mock_svc.upload_skill.assert_awaited_once()
            kwargs = mock_svc.upload_skill.await_args.kwargs
            assert kwargs["user_id"] == "bot-owner-1"
            assert "author_id" not in kwargs

    def test_upload_passes_bot_scope_to_layout_aware_factory(self, mock_ctx):
        with _upload_skill_di_app(
            mock_ctx,
            bot_status="ACTIVE",
        ) as (client, mock_svc, _, mock_factory):
            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            assert response.json()["success"] is True
            create_kwargs = mock_factory.create.call_args.kwargs
            assert create_kwargs["entity_id"] == mock_ctx.user_id
            assert create_kwargs["bot_id"] == mock_ctx.bot_id
            assert create_kwargs["engine_type"] == "openclaw"

    def test_upload_normalizes_runtime_unavailable_error_message(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (client, mock_svc, _, _):
            mock_svc.upload_skill.side_effect = ValueError(
                "Upload processing error: 502 Bad Gateway"
            )

            response = client.post(
                "/api/skills/upload",
                files=[
                    ("files", ("SKILL.md", b"---\nname: a\ndescription: a\n---", "text/markdown"))
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert response.status_code == 200
            assert body["success"] is False
            assert body["message"] == "当前 Bot 的运行环境暂不可用，请重新启动 Bot 后重试。"


class TestUploadSkillDesktopLiveStatus:
    """Desktop bots' DB status lags BaaS; the upload gate consults BaaS live
    status before rejecting. Cloud bots (personal/service) always trust DB."""

    def _post_upload(self, client):
        return client.post(
            "/api/skills/upload",
            files=[
                ("files", ("SKILL.md", b"---\nname: a\ndescription: a\n---", "text/markdown"))
            ],
            data={"file_paths": json.dumps(["SKILL.md"])},
        )

    def test_desktop_db_offline_but_baas_active_passes(self, mock_ctx):
        # The fix target: DB still OFFLINE (lag) but BaaS says ACTIVE → upload.
        mock_bot_service = MagicMock()
        mock_bot_service.resolve_desktop_live_status = MagicMock(return_value="ACTIVE")
        with _upload_skill_di_app(
            mock_ctx,
            bot_status="OFFLINE",
            bot_type="desktop",
            device_id="dev-1",
            bot_service=mock_bot_service,
        ) as (client, mock_svc, _, _):
            response = self._post_upload(client)
            body = response.json()
            assert body["success"] is True
            assert mock_svc.upload_skill.await_count == 1
            mock_bot_service.resolve_desktop_live_status.assert_called_once()

    def test_desktop_baas_failure_falls_back_to_db_and_rejects(self, mock_ctx):
        # BaaS unreachable → resolver returns None → DB OFFLINE → reject.
        mock_bot_service = MagicMock()
        mock_bot_service.resolve_desktop_live_status = MagicMock(return_value=None)
        with _upload_skill_di_app(
            mock_ctx,
            bot_status="OFFLINE",
            bot_type="desktop",
            device_id="dev-1",
            bot_service=mock_bot_service,
        ) as (client, mock_svc, _, _):
            response = self._post_upload(client)
            body = response.json()
            assert body["success"] is False
            assert "expected ACTIVE" in body["message"]
            mock_svc.upload_skill.assert_not_called()

    def test_desktop_baas_confirms_offline_still_rejects(self, mock_ctx):
        # BaaS itself says OFFLINE → keep rejecting (don't falsely pass).
        mock_bot_service = MagicMock()
        mock_bot_service.resolve_desktop_live_status = MagicMock(return_value="OFFLINE")
        with _upload_skill_di_app(
            mock_ctx,
            bot_status="OFFLINE",
            bot_type="desktop",
            device_id="dev-1",
            bot_service=mock_bot_service,
        ) as (client, mock_svc, _, _):
            response = self._post_upload(client)
            body = response.json()
            assert body["success"] is False
            assert "OFFLINE" in body["message"]
            mock_svc.upload_skill.assert_not_called()

    def test_desktop_pending_does_not_consult_baas(self, mock_ctx):
        # Process state: BaaS is unreliable, gate must trust DB and NOT query.
        mock_bot_service = MagicMock()
        mock_bot_service.resolve_desktop_live_status = MagicMock(return_value=None)
        with _upload_skill_di_app(
            mock_ctx,
            bot_status="PENDING",
            bot_type="desktop",
            device_id="dev-1",
            bot_service=mock_bot_service,
        ) as (client, mock_svc, _, _):
            response = self._post_upload(client)
            body = response.json()
            assert body["success"] is False
            assert "PENDING" in body["message"]
            mock_svc.upload_skill.assert_not_called()

    def test_cloud_bot_never_consults_baas(self, mock_ctx):
        # personal/service: gate uses DB directly, resolve_desktop_live_status
        # is never called (the if-branch is skipped entirely).
        mock_bot_service = MagicMock()
        mock_bot_service.resolve_desktop_live_status = MagicMock(return_value="ACTIVE")
        with _upload_skill_di_app(
            mock_ctx,
            bot_status="ACTIVE",
            bot_type="personal",
            bot_service=mock_bot_service,
        ) as (client, mock_svc, _, _):
            response = self._post_upload(client)
            body = response.json()
            assert body["success"] is True
            mock_bot_service.resolve_desktop_live_status.assert_not_called()


class TestActivateSkillAsyncAwait:
    """Test that activate_skill endpoint properly awaits service.activate_skill.

    BEFORE FIX: run_in_threadpool(service.activate_skill, ...) does not properly
    schedule the async coroutine. The mock's await_count will be 0.

    AFTER FIX: await service.activate_skill(...) is called directly.
    The mock's await_count will be 1.
    """

    def test_activate_skill_awaits_service_method(self, mock_ctx):
        with _skill_service_di_app(mock_ctx) as (client, mock_svc):
            client.post(
                "/api/skills/my-skill-id/activate",
                json={"source_path": "git://some/path"},
            )
            # If the bug exists: mock await_count == 0
            # After fix: mock await_count == 1
            assert mock_svc.activate_skill.await_count == 1, (
                f"activate_skill was not awaited! "
                f"await_count={mock_svc.activate_skill.await_count}"
            )

    def test_activate_skill_passes_correct_args(self, mock_ctx):
        """user_id/bolt_id must be passed as kwargs (not positional).

        Regression guard for the source_path-vs-user_id misalignment bug:
        before the fix, the endpoint called
        ``service.activate_skill(actual_skill_id, request.source_path)`` —
        that second positional argument lands in ``user_id``, poisoning
        the device_fs router. The fix uses kwargs to make the contract
        explicit and unambiguous.
        """
        with _skill_service_di_app(mock_ctx) as (client, mock_svc):
            client.post(
                "/api/skills/my-skill-id/activate",
                json={"source_path": "git://some/path"},
            )
            mock_svc.activate_skill.assert_called_once()
            call = mock_svc.activate_skill.call_args
            # user_id / bolt_id must be kwargs, not positional
            assert "user_id" in call.kwargs, (
                f"user_id must be a kwarg; got call={call}"
            )
            assert "bolt_id" in call.kwargs, (
                f"bolt_id must be a kwarg; got call={call}"
            )
            # And user_id must NOT be source_path — regression guard for
            # the position-arg-misalignment bug (see test docstring).
            assert call.kwargs["user_id"] != "git://some/path", (
                f"user_id was set to request.source_path ({call.kwargs['user_id']!r}) — "
                "this is the position-arg-misalignment bug coming back."
            )
            assert call.kwargs["user_id"] == mock_ctx.user_id, (
                f"user_id should be ctx.user_id ({mock_ctx.user_id!r}); "
                f"got {call.kwargs['user_id']!r}"
            )

    def test_activate_skill_fails_when_runtime_mapping_sync_fails(self, mock_ctx):
        with _skill_service_di_app(
            mock_ctx,
            device_sync_result={"success": False, "message": "source missing"},
            runtime_uses_pool_paths=True,
        ) as (client, mock_svc):
            response = client.post(
                "/api/skills/my-skill-id/activate",
                json={"source_path": "local://skills-local/my-skill"},
            )

            assert response.status_code == 502
            assert response.json()["detail"] == (
                "Failed to synchronize activated skills to runtime"
            )
            mock_svc.activate_skill.assert_awaited_once()


# ── deactivate_skill ─────────────────────────────────────────────────────────

class TestDeactivateSkillAsyncAwait:
    """Test that deactivate_skill endpoint properly awaits service.deactivate_skill.

    BEFORE FIX: service.deactivate_skill(skill_id) is called WITHOUT await,
    returning a coroutine that is never awaited. The mock's await_count will be 0.

    AFTER FIX: await service.deactivate_skill(skill_id) is called.
    The mock's await_count will be 1.
    """

    def test_deactivate_skill_awaits_service_method(self, mock_ctx):
        with _skill_service_di_app(mock_ctx) as (client, mock_svc):
            client.post("/api/skills/my-skill-id/deactivate")
            # If the bug exists: mock await_count == 0 (missing await)
            # After fix: mock await_count == 1
            assert mock_svc.deactivate_skill.await_count == 1, (
                f"deactivate_skill was not awaited! "
                f"await_count={mock_svc.deactivate_skill.await_count}"
            )

    def test_deactivate_skill_passes_correct_skill_id(self, mock_ctx):
        """user_id/bolt_id must be passed as kwargs (not positional)."""
        with _skill_service_di_app(mock_ctx) as (client, mock_svc):
            client.post("/api/skills/my-skill-id/deactivate")
            mock_svc.deactivate_skill.assert_called_once()
            call = mock_svc.deactivate_skill.call_args
            assert "user_id" in call.kwargs, (
                f"user_id must be a kwarg; got call={call}"
            )
            assert "bolt_id" in call.kwargs, (
                f"bolt_id must be a kwarg; got call={call}"
            )
            assert call.kwargs["user_id"] == mock_ctx.user_id, (
                f"user_id should be ctx.user_id ({mock_ctx.user_id!r}); "
                f"got {call.kwargs['user_id']!r}"
            )

    def test_deactivate_skill_fails_when_runtime_mapping_sync_fails(
        self, mock_ctx
    ):
        with _skill_service_di_app(
            mock_ctx,
            device_sync_result={"success": False, "message": "runtime unavailable"},
            runtime_uses_pool_paths=True,
        ) as (client, mock_svc):
            response = client.post("/api/skills/my-skill-id/deactivate")

            assert response.status_code == 502
            assert response.json()["detail"] == (
                "Failed to synchronize deactivated skills to runtime"
            )
            mock_svc.deactivate_skill.assert_awaited_once()


# ── activate_skills_batch ─────────────────────────────────────────────────────

class TestActivateSkillsBatchAsyncAwait:
    """Test that activate_skills_batch endpoint properly awaits service.activate_skills_batch.

    BEFORE FIX: run_in_threadpool(service.activate_skills_batch, ...) has the same
    bug as activate_skill. The mock's await_count will be 0.

    AFTER FIX: await service.activate_skills_batch(...) is called directly.
    The mock's await_count will be 1.
    """

    def test_activate_skills_batch_awaits_service_method(self, mock_ctx):
        with _skill_service_di_app(mock_ctx) as (client, mock_svc):
            client.post(
                "/api/skills/market/activate-batch",
                json={"skill_paths": ["git://path/a", "git://path/b"]},
            )
            # If the bug exists: mock await_count == 0
            # After fix: mock await_count == 1
            assert mock_svc.activate_skills_batch.await_count == 1, (
                f"activate_skills_batch was not awaited! "
                f"await_count={mock_svc.activate_skills_batch.await_count}"
            )

    def test_activate_skills_batch_passes_correct_skill_paths(self, mock_ctx):
        """skill_paths must propagate; user_id/bolt_id must be kwargs."""
        with _skill_service_di_app(mock_ctx) as (client, mock_svc):
            client.post(
                "/api/skills/market/activate-batch",
                json={"skill_paths": ["git://path/a", "git://path/b"]},
            )
            mock_svc.activate_skills_batch.assert_called_once()
            call = mock_svc.activate_skills_batch.call_args
            # skill_paths is the first positional arg
            assert call.args and call.args[0] == ["git://path/a", "git://path/b"], (
                f"skill_paths not propagated correctly; call={call}"
            )
            # user_id/bolt_id must be kwargs so SkillService.activate_skills_batch
            # can forward them to each inner activate_skill() call.
            assert "user_id" in call.kwargs, (
                f"user_id must be a kwarg; got call={call}"
            )
            assert "bolt_id" in call.kwargs, (
                f"bolt_id must be a kwarg; got call={call}"
            )
            assert call.kwargs["user_id"] == mock_ctx.user_id

    def test_activate_skills_batch_fails_when_runtime_mapping_sync_fails(
        self, mock_ctx
    ):
        with _skill_service_di_app(
            mock_ctx,
            device_sync_result={"success": False, "message": "source missing"},
            runtime_uses_pool_paths=True,
        ) as (client, mock_svc):
            response = client.post(
                "/api/skills/market/activate-batch",
                json={"skill_paths": ["git://path/a"]},
            )

            assert response.status_code == 502
            assert response.json()["detail"] == (
                "Failed to synchronize activated skills to runtime"
            )
            mock_svc.activate_skills_batch.assert_awaited_once()
