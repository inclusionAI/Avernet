"""Contract tests — http adapter 16 endpoint caller 走 resolver + dispatcher.

Task 3 of `docs/superpowers/plans/2026-06-15-device-sync-supplier-for-bot-cleanup.md`:
确认 ``adapters/http`` 下的 device_sync / device_fs caller 都通过
``DeviceContextResolver.resolve_for_bot(bot_id, user_id) → dispatcher.dispatch(ctx)``,
不再走旧的 ``supplier.for_bot`` / ``dispatcher.for_bot`` 闭包模式。

9 个核心 endpoint case 覆盖 16 caller(file_router 8 caller 选 2 代表性即可):
- A 组(device_sync_supplier → resolver + device_sync_dispatcher):
  * skills.py:867 activate_skill
  * skills.py:937 deactivate_skill
  * skills.py:1281 activate_skills_batch
  * skillsets.py:1378 sync_skills_to_device
- B 组(device_fs_dispatcher.for_bot → resolver + dispatch(ctx)):
  * identity/router.py:249 (_write_file_safely 经 endpoint 触发)
  * resources/router.py:587 delete_resource
  * resources/router.py:631 upload_files_legacy
  * resources/file_router.py:581 list_files
  * resources/file_router.py:670 upload_files
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import CallableProvider, Injector, Module

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.plugin_api.passport import PassportPlugin


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_ctx(bot_id: str = "bot-1", user_id: str = "user-1") -> DeviceContext:
    """工厂:生成稳定的 DeviceContext mock。"""
    return DeviceContext(
        provider="baas",
        conn_info={"engine_type": "openclaw"},
        binding_id=42,
        bot_id=bot_id,
        user_id=user_id,
    )


@pytest.fixture
def mock_request_ctx():
    return RequestContext(user_id="user-1", bot_id="bot-1")


def _make_mock_resolver(ctx: DeviceContext) -> MagicMock:
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = ctx
    return resolver


def _make_mock_path_factory(tmp_path):
    """Return a mock path_factory whose get_* methods return real Path objects.

    Real Path objects are needed because endpoint code calls .parent / .mkdir
    etc on them.
    """
    pf = MagicMock()
    pf.get_bot_skills_dir.return_value = tmp_path / "skills"
    pf.get_bot_skills_local_dir.return_value = tmp_path / "skills-local"
    pf.get_bot_engine_dir.return_value = tmp_path / "engine"
    pf.get_bot_skills_repo_dir.return_value = tmp_path / "skills-repo"
    pf.get_bot_workspace_dir = MagicMock(return_value=tmp_path / "workspace")
    return pf


# ─────────────────────────────────────────────────────────────────────────
# A 组 — skills.py / skillsets.py (device_sync_supplier → resolver + dispatcher)
# ─────────────────────────────────────────────────────────────────────────


@contextmanager
def _skills_router_app(mock_ctx, tmp_path):
    """Build a TestClient wired with mocked resolver + device_sync_dispatcher."""
    from agentclaw.community.adapters.http.skill_center.skills import (
        router as skills_router,
    )
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
    from agentclaw.community.di.modules.skill_center_module import (
        SkillSetServiceFactory,
    )
    from agentclaw.community.core.devices.services.device_sync_dispatcher import DeviceSyncDispatcher
    from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
    from agentclaw.community.api.skill_set_service_factory import (
        SkillSetServiceFactoryProtocol,
    )

    ctx = _make_ctx()
    resolver = _make_mock_resolver(ctx)

    sync_plugin = MagicMock()
    sync_plugin.sync_symlinks.return_value = {"success": True}
    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = sync_plugin

    # ─── Other deps used by the endpoints ───
    skill_service = MagicMock()
    skill_service.activate_skill = AsyncMock(return_value=True)
    skill_service.deactivate_skill = AsyncMock(return_value=True)
    skill_service.activate_skills_batch = AsyncMock(
        return_value={"success": [{"path": "a", "skill_id": "a", "link_name": "a"}], "failed": []}
    )
    skill_service.get_link_name = MagicMock(return_value="link_name")

    skill_service_factory = MagicMock()
    skill_service_factory.create.return_value = skill_service

    skill_set_service = MagicMock()
    skill_set_service.get_symlink_mappings.return_value = []
    skill_set_service_factory = MagicMock()
    skill_set_service_factory.create.return_value = skill_set_service

    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {
        "bot_id": "bot-1",
        "owner_id": "user-1",
        "active_engine": "openclaw",
        "bot_type": "personal",
        "status": "ACTIVE",
    }
    bot_repo.list_by_owner.return_value = (0, [])

    path_factory = _make_mock_path_factory(tmp_path)

    app = FastAPI()
    app.include_router(skills_router)
    app.dependency_overrides[get_request_context] = lambda: mock_ctx

    class _TestModule(Module):
        def configure(self, binder):
            binder.bind(BotRepository, to=bot_repo)
            binder.bind(WorkspacePathFactory, to=path_factory)
            binder.bind(
                SkillServiceFactoryProtocol, to=skill_service_factory
            )
            binder.bind(
                SkillSetServiceFactoryProtocol, to=skill_set_service_factory
            )
            binder.bind(SkillSetServiceFactory, to=skill_set_service_factory)
            binder.bind(DeviceContextResolver, to=resolver)
            binder.bind(DeviceSyncDispatcher, to=dispatcher)

    injector = Injector([_TestModule()])
    attach_injector(app, injector)

    client = TestClient(app, raise_server_exceptions=False)
    yield client, resolver, dispatcher, sync_plugin


@contextmanager
def _skillsets_router_app(mock_ctx, tmp_path):
    from agentclaw.community.adapters.http.skill_center.skillsets import (
        router as skillsets_router,
    )
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
    from agentclaw.community.di.modules.skill_center_module import (
        SkillSetServiceFactory,
    )
    from agentclaw.community.core.devices.services.device_sync_dispatcher import DeviceSyncDispatcher
    from agentclaw.community.api.skill_set_service_factory import (
        SkillSetServiceFactoryProtocol,
    )

    ctx = _make_ctx()
    resolver = _make_mock_resolver(ctx)

    sync_plugin = MagicMock()
    sync_plugin.sync_symlinks.return_value = {"success": True}
    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = sync_plugin

    skill_set_service = MagicMock()
    skill_set_service.get_symlink_mappings.return_value = []
    skill_set_service_factory = MagicMock()
    skill_set_service_factory.create.return_value = skill_set_service

    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {
        "bot_id": "bot-1",
        "owner_id": "user-1",
        "active_engine": "openclaw",
    }

    path_factory = _make_mock_path_factory(tmp_path)

    app = FastAPI()
    app.include_router(skillsets_router)
    app.dependency_overrides[get_request_context] = lambda: mock_ctx

    class _TestModule(Module):
        def configure(self, binder):
            binder.bind(BotRepository, to=bot_repo)
            binder.bind(WorkspacePathFactory, to=path_factory)
            binder.bind(
                SkillSetServiceFactoryProtocol, to=skill_set_service_factory
            )
            binder.bind(SkillSetServiceFactory, to=skill_set_service_factory)
            binder.bind(DeviceContextResolver, to=resolver)
            binder.bind(DeviceSyncDispatcher, to=dispatcher)

    injector = Injector([_TestModule()])
    attach_injector(app, injector)

    client = TestClient(app, raise_server_exceptions=False)
    yield client, resolver, dispatcher, sync_plugin


class TestSkillsEndpointsUseResolver:
    def test_activate_skill_endpoint_uses_resolver(self, mock_request_ctx, tmp_path):
        with _skills_router_app(mock_request_ctx, tmp_path) as (
            client,
            resolver,
            dispatcher,
            _sync_plugin,
        ):
            resp = client.post(
                "/api/skills/sk-x/activate",
                json={"source_path": "sk-x"},
                params={
                    "entity_id": "user-1",
                    "entity_type": "staff",
                    "bot_id": "bot-1",
                    "engine_type": "openclaw",
                },
            )
            assert resp.status_code == 200, resp.text
            resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
            dispatcher.dispatch.assert_called_once()
            assert dispatcher.dispatch.call_args[0][0] is resolver.resolve_for_bot.return_value

    def test_deactivate_skill_endpoint_uses_resolver(self, mock_request_ctx, tmp_path):
        with _skills_router_app(mock_request_ctx, tmp_path) as (
            client,
            resolver,
            dispatcher,
            _sync_plugin,
        ):
            resp = client.post(
                "/api/skills/sk-x/deactivate",
                params={
                    "entity_id": "user-1",
                    "entity_type": "staff",
                    "bot_id": "bot-1",
                    "engine_type": "openclaw",
                },
            )
            assert resp.status_code == 200, resp.text
            resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
            dispatcher.dispatch.assert_called_once()

    def test_activate_skills_batch_endpoint_uses_resolver(self, mock_request_ctx, tmp_path):
        with _skills_router_app(mock_request_ctx, tmp_path) as (
            client,
            resolver,
            dispatcher,
            _sync_plugin,
        ):
            resp = client.post(
                "/api/skills/market/activate-batch",
                json={"skill_paths": ["sk-x"]},
                params={
                    "entity_id": "user-1",
                    "entity_type": "staff",
                    "bot_id": "bot-1",
                    "engine_type": "openclaw",
                },
            )
            # Endpoint may end up 500 if response shape doesn't match the schema;
            # what matters for this contract test is that resolver was called
            # before any device sync.
            resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
            dispatcher.dispatch.assert_called_once()


class TestSkillsetsEndpointUsesResolver:
    def test_sync_skills_to_device_endpoint_uses_resolver(
        self, mock_request_ctx, tmp_path
    ):
        with _skillsets_router_app(mock_request_ctx, tmp_path) as (
            client,
            resolver,
            dispatcher,
            _sync_plugin,
        ):
            resp = client.post(
                "/api/skillsets/sync-skills",
                params={
                    "entity_id": "user-1",
                    "entity_type": "staff",
                    "bot_id": "bot-1",
                    "engine_type": "openclaw",
                },
            )
            # Resolver must be called regardless of final response status.
            resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
            dispatcher.dispatch.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────
# B 组 — identity / resources (device_fs_dispatcher.for_bot → resolver + dispatch)
# ─────────────────────────────────────────────────────────────────────────


@contextmanager
def _identity_router_app(mock_ctx, tmp_path, monkeypatch):
    """TestClient for identity router that hits _write_file_safely (the
    Arca branch in particular).
    """
    from agentclaw.community.adapters.http.identity.router import router as identity_router
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.services.identity import IdentityService
    from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
    from agentclaw.community.di.modules.skill_center_module import (
        DeviceFilesystemDispatcher,
    )

    ctx = _make_ctx()
    resolver = _make_mock_resolver(ctx)

    device_fs = MagicMock()
    device_fs.write_file = AsyncMock(return_value=True)
    device_fs.read_file = AsyncMock(return_value=b"")
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs

    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {
        "bot_id": "bot-1",
        "owner_id": "user-1",
        "active_engine": "openclaw",
    }

    path_factory = _make_mock_path_factory(tmp_path)

    app = FastAPI()
    app.include_router(identity_router)
    app.dependency_overrides[get_request_context] = lambda: mock_ctx

    identity_service = IdentityService(
        path_factory=path_factory,
        publish_repo=MagicMock(),
        bot_repo=bot_repo,
        resolver=resolver,
        device_fs_dispatcher=dispatcher,
    )

    class _TestModule(Module):
        def configure(self, binder):
            binder.bind(BotRepository, to=bot_repo)
            binder.bind(WorkspacePathFactory, to=path_factory)
            binder.bind(DeviceContextResolver, to=resolver)
            binder.bind(DeviceFilesystemDispatcher, to=dispatcher)
            binder.bind(IdentityService, to=identity_service)

    injector = Injector([_TestModule()])
    attach_injector(app, injector)

    client = TestClient(app, raise_server_exceptions=False)
    yield client, resolver, dispatcher, device_fs


class TestIdentityRouterUsesResolver:
    def test_write_file_safely_uses_resolver(
        self, mock_request_ctx, tmp_path, monkeypatch
    ):
        """Identity write endpoint → IdentityService.write_identity_file →
        resolver.resolve_for_bot + dispatcher.dispatch_addressed (provider-blind),
        not the old for_bot / get_device_info path.

        触发路径：PUT /api/identity/.../bot/{bot_id}/{file_type}。
        """
        with _identity_router_app(mock_request_ctx, tmp_path, monkeypatch) as (
            client,
            resolver,
            dispatcher,
            device_fs,
        ):
            # Hit the bot-level write endpoint (uses _write_file_safely
            # with bot_id+owner_id, which triggers the Arca branch).
            # PUT /api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}
            resp = client.put(
                "/api/identity/staff/user-1/bot/bot-1/RULES.md",
                json={"content": "hello"},
            )
            # We don't strictly need 200 — the assertion that matters is
            # the resolver call. (Endpoint may 4xx if other deps fail,
            # but the resolver path must have run before that.)
            # NOTE: RULES.md is in REFERENCE_FILES, so the endpoint also
            # syncs AGENTS.md → resolve/dispatch run more than once.
            # >=1 call with right args is the contract.
            assert resolver.resolve_for_bot.call_count >= 1
            resolver.resolve_for_bot.assert_any_call("bot-1", "user-1")
            assert dispatcher.dispatch_addressed.call_count >= 1


# ── resources/router.py ──────────────────────────────────────────────


@contextmanager
def _resources_router_app(mock_ctx, tmp_path):
    from agentclaw.community.adapters.http.resources.router import router as resources_router
    from agentclaw.community.adapters.http.resources.file_router import (
        router as file_router,
    )
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.resources.repository.protocol import (
        ResourceRepositoryProtocol,
    )
    from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
    from agentclaw.community.di.modules.skill_center_module import (
        DeviceFilesystemDispatcher,
    )
    from agentclaw.community.api.baas_service import BaasServiceProtocol
    from agentclaw.community.core.files.factory import BotFileServiceFactory
    from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
        BotPublishRepositoryProtocol,
    )

    ctx = _make_ctx()
    resolver = _make_mock_resolver(ctx)

    device_fs = MagicMock()
    device_fs.write_file = AsyncMock(return_value=True)
    device_fs.delete_file = AsyncMock(return_value=True)
    device_fs.list_dir = AsyncMock(return_value=[])
    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = device_fs
    dispatcher.dispatch_addressed.return_value = device_fs

    bot_repo = MagicMock()
    # Force non-arca / non-baas (local) so we hit the device_fs branch.
    bot_repo.get_device_provider_by_bot_id_and_owner.return_value = {
        "device_provider": "local",
        "sandbox_id": None,
    }
    bot_repo.get_by_id_and_owner.return_value = {
        "bot_id": "bot-1",
        "owner_id": "user-1",
        "active_engine": "openclaw",
        "bot_type": "personal",
    }

    resource_repo = MagicMock()
    # delete_resource needs a resource to exist
    fake_resource = MagicMock()
    fake_resource.is_link = False
    fake_resource.link_type = None
    fake_resource.is_directory = False

    path_factory = _make_mock_path_factory(tmp_path)
    baas_service = MagicMock()
    bot_file_factory = MagicMock()
    publish_repo = MagicMock()

    app = FastAPI()
    app.include_router(resources_router)
    app.dependency_overrides[get_request_context] = lambda: mock_ctx

    class _TestModule(Module):
        def configure(self, binder):
            binder.bind(BotRepository, to=bot_repo)
            binder.bind(
                ResourceRepositoryProtocol,
                to=CallableProvider(lambda: resource_repo),
            )
            binder.bind(WorkspacePathFactory, to=path_factory)
            binder.bind(DeviceContextResolver, to=resolver)
            binder.bind(DeviceFilesystemDispatcher, to=dispatcher)
            binder.bind(
                BaasServiceProtocol, to=CallableProvider(lambda: baas_service)
            )
            binder.bind(BotFileServiceFactory, to=bot_file_factory)
            binder.bind(
                BotPublishRepositoryProtocol,
                to=CallableProvider(lambda: publish_repo),
            )
            # delete_resource injects PassportPlugin (yuque perm sync); not
            # exercised here (fake_resource is not a yuque link) but DI must resolve it.
            binder.bind(
                PassportPlugin, to=CallableProvider(lambda: MagicMock())
            )

    injector = Injector([_TestModule()])
    attach_injector(app, injector)

    client = TestClient(app, raise_server_exceptions=False)
    yield client, resolver, dispatcher, device_fs, resource_repo, fake_resource


class TestResourcesRouterUsesResolver:
    def test_delete_resource_endpoint_uses_resolver(self, mock_request_ctx, tmp_path):
        """resources/router.py:587 — delete_resource 走 resolver+dispatch。"""
        with _resources_router_app(mock_request_ctx, tmp_path) as (
            client,
            resolver,
            dispatcher,
            device_fs,
            resource_repo,
            fake_resource,
        ):
            # Make the legacy_svc.get_resource return a real-ish thing via
            # a get_legacy_resource_service patch is overkill; instead the
            # test gives up reaching final status and just verifies the
            # resolver call happened during the request lifecycle.
            resp = client.delete(
                "/api/resources/some-resource-id",
                params={
                    "entity_id": "user-1",
                    "entity_type": "staff",
                    "bot_id": "bot-1",
                    "engine_type": "openclaw",
                },
            )
            # Resolver MUST be called once for ctx → dispatcher.dispatch(ctx).
            resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
            dispatcher.dispatch.assert_called_once()
            assert dispatcher.dispatch.call_args[0][0] is resolver.resolve_for_bot.return_value

    def test_upload_files_legacy_endpoint_addresses_workspace_data_namespace(
        self, mock_request_ctx, tmp_path
    ):
        """The legacy upload must not send a root-relative host path to ARCA."""
        with _resources_router_app(mock_request_ctx, tmp_path) as (
            client,
            resolver,
            dispatcher,
            device_fs,
            resource_repo,
            fake_resource,
        ):
            resp = client.post(
                "/api/resources/file",
                files=[("files", ("test.txt", b"hello", "text/plain"))],
                params={
                    "entity_id": "user-1",
                    "entity_type": "staff",
                    "bot_id": "bot-1",
                    "engine_type": "openclaw",
                    "parent_path": "",
                },
            )
            resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
            dispatcher.dispatch_addressed.assert_called_once_with(
                resolver.resolve_for_bot.return_value,
                namespace="workspace",
                entity_type="staff",
                entity_id="user-1",
                bot_id="bot-1",
                engine_type="openclaw",
            )
            device_fs.write_file.assert_awaited_once_with(
                "workspace/data/test.txt", b"hello"
            )


# ── resources/file_router.py ─────────────────────────────────────────


@contextmanager
def _file_router_app(mock_ctx, tmp_path):
    from agentclaw.community.adapters.http.resources.file_router import (
        router as file_router,
    )
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
    from agentclaw.community.di.modules.skill_center_module import (
        DeviceFilesystemDispatcher,
    )
    from agentclaw.community.api.baas_service import BaasServiceProtocol
    from agentclaw.community.core.files.factory import BotFileServiceFactory
    from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
        BotPublishRepositoryProtocol,
    )

    ctx = _make_ctx()
    resolver = _make_mock_resolver(ctx)

    device_fs = MagicMock()
    device_fs.write_file = AsyncMock(return_value=True)
    device_fs.delete_file = AsyncMock(return_value=True)
    device_fs.list_dir = AsyncMock(return_value=[])
    dispatcher = MagicMock()
    # file_router now delegates to ResourceFileService, which addresses files by the
    # workspace namespace via dispatch_addressed (the generic dispatch is unused here).
    dispatcher.dispatch_addressed.return_value = device_fs
    dispatcher.dispatch.return_value = device_fs

    bot_repo = MagicMock()
    bot_repo.get_device_provider_by_bot_id_and_owner.return_value = {
        "device_provider": "local",
        "sandbox_id": None,
    }
    bot_repo.get_by_id_and_owner.return_value = {
        "bot_id": "bot-1",
        "owner_id": "user-1",
        "active_engine": "openclaw",
        "bot_type": "personal",
    }

    path_factory = _make_mock_path_factory(tmp_path)
    baas_service = MagicMock()
    bot_file_factory = MagicMock()
    publish_repo = MagicMock()

    app = FastAPI()
    app.include_router(file_router)
    app.dependency_overrides[get_request_context] = lambda: mock_ctx

    class _TestModule(Module):
        def configure(self, binder):
            binder.bind(BotRepository, to=bot_repo)
            binder.bind(WorkspacePathFactory, to=path_factory)
            binder.bind(DeviceContextResolver, to=resolver)
            binder.bind(DeviceFilesystemDispatcher, to=dispatcher)
            binder.bind(
                BaasServiceProtocol, to=CallableProvider(lambda: baas_service)
            )
            binder.bind(BotFileServiceFactory, to=bot_file_factory)
            binder.bind(
                BotPublishRepositoryProtocol,
                to=CallableProvider(lambda: publish_repo),
            )
            # delete_resource injects PassportPlugin (yuque perm sync); not
            # exercised here (fake_resource is not a yuque link) but DI must resolve it.
            binder.bind(
                PassportPlugin, to=CallableProvider(lambda: MagicMock())
            )

    injector = Injector([_TestModule()])
    attach_injector(app, injector)

    client = TestClient(app, raise_server_exceptions=False)
    yield client, resolver, dispatcher, device_fs


class TestFileRouterUsesResolver:
    def test_list_files_endpoint_uses_resolver(self, mock_request_ctx, tmp_path):
        """resources/file_router.py:581 — list_files 走 resolver+dispatch。"""
        with _file_router_app(mock_request_ctx, tmp_path) as (
            client,
            resolver,
            dispatcher,
            device_fs,
        ):
            resp = client.get(
                "/files",
                params={
                    "path": "",
                    "bot_id": "bot-1",
                    "engine_type": "openclaw",
                    "owner_id": "user-1",
                },
            )
            resolver.resolve_for_bot.assert_called_once_with(
                "bot-1", "user-1", device_uuid=None
            )
            dispatcher.dispatch_addressed.assert_called_once()

    def test_upload_files_endpoint_uses_resolver(self, mock_request_ctx, tmp_path):
        """resources/file_router.py:670 — upload_files 走 resolver+dispatch。"""
        with _file_router_app(mock_request_ctx, tmp_path) as (
            client,
            resolver,
            dispatcher,
            device_fs,
        ):
            resp = client.post(
                "/files/upload",
                files=[("files", ("test.txt", b"hello", "text/plain"))],
                params={
                    "path": "",
                    "bot_id": "bot-1",
                    "engine_type": "openclaw",
                    "owner_id": "user-1",
                },
            )
            resolver.resolve_for_bot.assert_called_once_with(
                "bot-1", "user-1", device_uuid=None
            )
            dispatcher.dispatch_addressed.assert_called_once()
