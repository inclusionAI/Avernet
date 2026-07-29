"""Endpoint coverage for the teclaw branches of the readme / delete / parameters
routes — they resolve the provider and build the skill service with the teclaw
path adapter. A teclaw-provider resolver drives those branches end-to-end."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.adapters.http.skill_center.skills import router as skills_router

pytestmark = pytest.mark.unit

_CTX = RequestContext(user_id="u1", bot_id="b1", nick_name=None)
_Q = {"entity_id": "u1", "bot_id": "b1", "engine_type": "openclaw"}


def _app(skill_service):
    """Build a TestClient with a teclaw resolver and the given mock skill service."""
    factory = MagicMock()
    factory.create.return_value = skill_service

    path_factory = MagicMock()
    path_factory.get_bot_skills_dir.return_value = MagicMock()
    path_factory.get_bot_skills_local_dir.return_value = MagicMock()
    path_factory.get_bot_engine_dir.return_value = MagicMock()
    path_factory.get_bot_skills_repo_dir.return_value = MagicMock()

    bot_repo = MagicMock()
    default_bot = {
        "bot_id": "b1", "owner_id": "u1", "active_engine": "openclaw",
        "bot_type": "service", "status": "ACTIVE",
    }
    bot_repo.get_by_id_and_owner.return_value = default_bot
    bot_repo.get_by_id.return_value = default_bot

    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = SimpleNamespace(provider="teclaw")

    skill_repo = MagicMock()
    skill_repo.get_by_id.return_value = {
        "id": "1",
        "name": "x",
        "link_name": "x",
        "git_path": "local://skills-local/x",
        "bolt_id": "b1",
        "user_id": "u1",
    }

    param_service = MagicMock()
    param_service.get_skill_parameters.return_value = {}
    param_service.save_skill_parameters = AsyncMock(return_value=False)
    param_factory = MagicMock()
    param_factory.create = AsyncMock(return_value=param_service)

    center_client = MagicMock()
    lock_service = MagicMock()
    lock_service.get_lock_info.return_value = SimpleNamespace(
        has_collaborators=True,
        lock=SimpleNamespace(holder_user_id="u1"),
        holder_name="actor",
    )
    collaborator_service = MagicMock()
    collaborator_service.check_collaborator_permission.return_value = {
        "has_permission": True,
        "level": "ADMIN",
    }

    app = FastAPI()
    app.include_router(skills_router)
    app.dependency_overrides[get_request_context] = lambda: _CTX

    class _M(Module):
        def configure(self, binder):
            from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
            from agentclaw.community.api.skill_parameter_service_factory import (
                SkillParameterServiceFactoryProtocol,
            )
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
            from agentclaw.community.core.skill_center.services.repositories import SkillRepository
            from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
            from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient

            binder.bind(SkillServiceFactory, to=factory)
            binder.bind(SkillServiceFactoryProtocol, to=factory)
            binder.bind(WorkspacePathFactory, to=path_factory)
            binder.bind(BotRepository, to=bot_repo)
            binder.bind(DeviceContextResolver, to=resolver)
            binder.bind(SkillRepository, to=skill_repo)
            binder.bind(SkillCenterClient, to=center_client)
            binder.bind(SkillParameterServiceFactoryProtocol, to=param_factory)
            binder.bind(CollaboratorLockService, to=lock_service)
            binder.bind(CollaboratorService, to=collaborator_service)

    attach_injector(app, Injector([_M()]))
    return TestClient(app, raise_server_exceptions=False), factory, path_factory


def test_readme_route_teclaw_branch():
    svc = MagicMock()
    svc.get_skill.return_value = {"git_path": "local://skills-local/x", "name": "x"}
    svc.get_skill_by_link_name.return_value = None
    svc.get_skill_readme = AsyncMock(return_value="# readme")
    client, factory, path_factory = _app(svc)
    resp = client.get("/api/skills/1/readme", params=_Q)
    assert resp.status_code == 200
    # teclaw branch: service built with an adapter; local_dir resolved is_teclaw=True
    assert factory.create.call_args.kwargs["local_skill_path_adapter"] is not None
    assert path_factory.get_bot_skills_local_dir.call_args.kwargs["is_teclaw"] is True


def test_readme_route_uses_skill_owner_context_for_teclaw():
    lookup_svc = MagicMock()
    lookup_svc.get_skill.return_value = {
        "id": "1",
        "name": "x",
        "link_name": "x",
        "git_path": "local://skills-local/x",
        "bolt_id": "teclaw-bot",
        "user_id": "owner-u",
    }
    read_svc = MagicMock()
    read_svc.get_skill_readme = AsyncMock(return_value="# readme")

    client, factory, path_factory = _app(lookup_svc)
    factory.create.side_effect = [lookup_svc, read_svc]

    resp = client.get(
        "/api/skills/1/readme",
        params={"entity_id": "viewer-u", "bot_id": "default", "engine_type": "openclaw"},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["content"] == "# readme"
    assert factory.create.call_count == 2
    assert factory.create.call_args.kwargs["local_skill_path_adapter"] is not None
    assert path_factory.get_bot_skills_local_dir.call_args.args[:4] == (
        "owner-u",
        "teclaw-bot",
        "openclaw",
        "staff",
    )
    assert path_factory.get_bot_skills_local_dir.call_args.kwargs["is_teclaw"] is True
    read_svc.get_skill_readme.assert_awaited_once_with("1", "owner-u", "teclaw-bot")


def test_readme_route_link_name_falls_back_to_global_lookup():
    lookup_svc = MagicMock()
    lookup_svc.get_skill_by_link_name.side_effect = [
        None,
        None,
        {
            "id": "2",
            "name": "x",
            "link_name": "x",
            "git_path": "local://skills-local/x",
            "bolt_id": "teclaw-bot",
            "user_id": "owner-u",
        },
    ]
    read_svc = MagicMock()
    read_svc.get_skill_readme = AsyncMock(return_value="# readme")

    client, factory, _ = _app(lookup_svc)
    factory.create.side_effect = [lookup_svc, read_svc]

    resp = client.get(
        "/api/skills/x/readme",
        params={"entity_id": "viewer-u", "bot_id": "default", "engine_type": "openclaw"},
    )

    assert resp.status_code == 200
    lookup_svc.get_skill_by_link_name.assert_any_call("x", bolt_id="default")
    lookup_svc.get_skill_by_link_name.assert_any_call("x", bolt_id="b1")
    lookup_svc.get_skill_by_link_name.assert_any_call("x", bolt_id=None)
    read_svc.get_skill_readme.assert_awaited_once_with("x", "owner-u", "teclaw-bot")


def test_readme_route_handles_duplicate_link_name_scopes_and_desktop_bot():
    lookup_svc = MagicMock()
    lookup_svc.get_skill_by_link_name.side_effect = [
        None,
        {
            "id": "3",
            "name": "x",
            "link_name": "x",
            "git_path": "local://skills-local/x",
            "bolt_id": "desktop-bot",
            "user_id": "owner-u",
        },
    ]
    read_svc = MagicMock()
    read_svc.get_skill_readme = AsyncMock(return_value="# readme")

    client, factory, path_factory = _app(lookup_svc)
    factory.create.side_effect = [lookup_svc, read_svc]
    bot_repo = client.app.state.injector.get(
        __import__(
            "agentclaw.community.core.bot_management.repository.protocol",
            fromlist=["BotRepository"],
        ).BotRepository
    )
    bot_repo.get_by_id_and_owner.return_value = {
        "bot_id": "desktop-bot",
        "owner_id": "owner-u",
        "active_engine": "openclaw",
        "bot_type": "desktop",
        "status": "ACTIVE",
    }

    resp = client.get(
        "/api/skills/x/readme",
        params={"entity_id": "u1", "bot_id": "b1", "engine_type": "openclaw"},
    )

    assert resp.status_code == 200
    lookup_svc.get_skill_by_link_name.assert_any_call("x", bolt_id="b1")
    # effective_bot_id and ctx.bot_id are the same, so duplicate scope is skipped.
    assert lookup_svc.get_skill_by_link_name.call_count == 2
    assert path_factory.get_bot_skills_local_dir.call_args.kwargs["is_desktop"] is True


def test_readme_route_falls_back_when_skill_not_found_and_bot_type_lookup_fails():
    lookup_svc = MagicMock()
    lookup_svc.get_skill.return_value = None
    lookup_svc.get_skill_by_link_name.return_value = None
    read_svc = MagicMock()
    read_svc.get_skill_readme = AsyncMock(return_value="# fallback")

    client, factory, _ = _app(lookup_svc)
    factory.create.side_effect = [lookup_svc, read_svc]
    bot_repo = client.app.state.injector.get(
        __import__(
            "agentclaw.community.core.bot_management.repository.protocol",
            fromlist=["BotRepository"],
        ).BotRepository
    )
    bot_repo.get_by_id_and_owner.side_effect = [
        {"bot_type": "service", "active_engine": "openclaw"},
        RuntimeError("boom"),
        RuntimeError("boom"),
    ]

    resp = client.get("/api/skills/404/readme", params=_Q)

    assert resp.status_code == 200
    read_svc.get_skill_readme.assert_awaited_once_with("404", "u1", "b1")


def test_readme_route_numeric_id_falls_back_to_link_name_when_id_missing():
    lookup_svc = MagicMock()
    lookup_svc.get_skill.return_value = None
    lookup_svc.get_skill_by_link_name.return_value = {
        "id": "999",
        "name": "numeric-link",
        "link_name": "123",
        "git_path": "local://skills-local/numeric-link",
        "bolt_id": "teclaw-bot",
        "user_id": "owner-u",
    }
    read_svc = MagicMock()
    read_svc.get_skill_readme = AsyncMock(return_value="# numeric")

    client, factory, _ = _app(lookup_svc)
    factory.create.side_effect = [lookup_svc, read_svc]

    resp = client.get("/api/skills/123/readme", params=_Q)

    assert resp.status_code == 200
    lookup_svc.get_skill.assert_called_once_with("123")
    lookup_svc.get_skill_by_link_name.assert_called_once_with("123", bolt_id="b1")
    read_svc.get_skill_readme.assert_awaited_once_with("123", "owner-u", "teclaw-bot")


@pytest.mark.parametrize("method", ["get", "post"])
def test_parameter_routes_use_trusted_bot_owner_for_device_resolution(method):
    """ADMIN 协作者操作参数时，设备解析必须使用 Bot owner 而非 actor。"""

    lookup_svc = MagicMock()
    lookup_svc.get_skill.return_value = {
        "id": "1",
        "name": "x",
        "link_name": "x",
        "git_path": "local://skills-local/x",
        "bolt_id": "b1",
        "user_id": "owner-u",
    }
    lookup_svc.parse_local_skill_config = AsyncMock(return_value=None)
    client, _, _ = _app(lookup_svc)
    injector = client.app.state.injector

    from agentclaw.community.api.skill_parameter_service_factory import (
        SkillParameterServiceFactoryProtocol,
    )
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.skill_center.services.repositories import SkillRepository

    bot_repo = injector.get(BotRepository)
    bot_repo.get_by_id.return_value = {
        "bot_id": "b1",
        "owner_id": "owner-u",
        "entity_id": "owner-u",
        "active_engine": "openclaw",
        "bot_type": "personal",
        "status": "ACTIVE",
    }
    skill_repo = injector.get(SkillRepository)
    skill_repo.get_by_id.return_value = lookup_svc.get_skill.return_value
    parameter_factory = injector.get(SkillParameterServiceFactoryProtocol)

    if method == "get":
        response = client.get("/api/skills/1/parameters", params=_Q)
    else:
        response = client.post(
            "/api/skills/1/parameters",
            params=_Q,
            json={"parameters": {}},
        )

    assert response.status_code == 200, response.text
    parameter_factory.create.assert_awaited_once_with(
        bot_id="b1",
        user_id="owner-u",
    )


def test_parameter_route_rejects_request_bot_mismatch_before_device_access():
    """skill 与请求 Bot 不一致时 fail closed，不能拨号到请求指定的 Bot。"""

    lookup_svc = MagicMock()
    lookup_svc.get_skill.return_value = {
        "id": "1",
        "name": "x",
        "link_name": "x",
        "git_path": "local://skills-local/x",
        "bolt_id": "b1",
        "user_id": "owner-u",
    }
    client, _, _ = _app(lookup_svc)
    injector = client.app.state.injector

    from agentclaw.community.api.skill_parameter_service_factory import (
        SkillParameterServiceFactoryProtocol,
    )
    from agentclaw.community.core.skill_center.services.repositories import SkillRepository

    injector.get(SkillRepository).get_by_id.return_value = lookup_svc.get_skill.return_value
    parameter_factory = injector.get(SkillParameterServiceFactoryProtocol)

    response = client.get(
        "/api/skills/1/parameters",
        params={**_Q, "bot_id": "other-bot"},
    )

    assert response.status_code == 409
    parameter_factory.create.assert_not_awaited()


@pytest.mark.parametrize("method", ["get", "post"])
def test_parameter_routes_reject_non_admin_collaborator(method):
    """member/无权限用户在触发设备访问前返回 403。"""

    lookup_svc = MagicMock()
    skill = {
        "id": "1",
        "name": "x",
        "link_name": "x",
        "git_path": "local://skills-local/x",
        "bolt_id": "b1",
        "user_id": "owner-u",
    }
    lookup_svc.get_skill.return_value = skill
    client, _, _ = _app(lookup_svc)
    injector = client.app.state.injector

    from agentclaw.community.api.skill_parameter_service_factory import (
        SkillParameterServiceFactoryProtocol,
    )
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
        CollaboratorService,
    )
    from agentclaw.community.core.skill_center.services.repositories import SkillRepository

    injector.get(SkillRepository).get_by_id.return_value = skill
    injector.get(BotRepository).get_by_id.return_value = {
        "bot_id": "b1",
        "owner_id": "owner-u",
        "entity_id": "owner-u",
        "active_engine": "openclaw",
        "bot_type": "personal",
    }
    injector.get(
        CollaboratorService
    ).check_collaborator_permission.return_value = {
        "has_permission": False,
        "level": "MEMBER",
    }
    parameter_factory = injector.get(SkillParameterServiceFactoryProtocol)

    if method == "get":
        response = client.get("/api/skills/1/parameters", params=_Q)
    else:
        response = client.post(
            "/api/skills/1/parameters",
            params=_Q,
            json={"parameters": {}},
        )

    assert response.status_code == 403
    parameter_factory.create.assert_not_awaited()


def test_parameter_route_returns_structured_error_without_active_binding():
    """真实无 active binding 时应返回结构化 409，而不是裸 500。"""

    from agentclaw.community.api.skill_parameter_service_factory import (
        SkillParameterServiceFactoryProtocol,
    )
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.devices.services.device_context import (
        DeviceNotBoundError,
    )
    from agentclaw.community.core.skill_center.services.repositories import SkillRepository

    lookup_svc = MagicMock()
    skill = {
        "id": "1",
        "name": "x",
        "link_name": "x",
        "git_path": "local://skills-local/x",
        "bolt_id": "b1",
        "user_id": "u1",
    }
    lookup_svc.get_skill.return_value = skill
    client, _, _ = _app(lookup_svc)
    injector = client.app.state.injector
    injector.get(SkillRepository).get_by_id.return_value = skill
    injector.get(BotRepository).get_by_id.return_value = {
        "bot_id": "b1",
        "owner_id": "u1",
        "entity_id": "u1",
        "active_engine": "openclaw",
        "bot_type": "personal",
    }
    injector.get(
        SkillParameterServiceFactoryProtocol
    ).create.side_effect = DeviceNotBoundError("missing")

    response = client.get("/api/skills/1/parameters", params=_Q)

    assert response.status_code == 409
    assert response.json()["detail"] == "Bot has no active device"


def test_shared_git_skill_parameters_use_requested_bot_owner():
    """共享 Git Skill 无 Skill owner 时，仍以目标 Bot owner 解析设备。"""

    from agentclaw.community.api.skill_parameter_service_factory import (
        SkillParameterServiceFactoryProtocol,
    )
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.skill_center.services.repositories import SkillRepository

    lookup_svc = MagicMock()
    skill = {
        "id": "1",
        "name": "shared",
        "link_name": "shared",
        "git_path": "git://shared",
        "bolt_id": None,
        "user_id": None,
        "is_public": True,
    }
    lookup_svc.get_skill.return_value = skill
    client, _, _ = _app(lookup_svc)
    injector = client.app.state.injector
    injector.get(SkillRepository).get_by_id.return_value = skill
    injector.get(BotRepository).get_by_id.return_value = {
        "bot_id": "b1",
        "owner_id": "owner-u",
        "entity_id": "owner-u",
        "active_engine": "openclaw",
        "bot_type": "personal",
    }
    parameter_factory = injector.get(SkillParameterServiceFactoryProtocol)

    response = client.get("/api/skills/1/parameters", params=_Q)

    assert response.status_code == 200
    parameter_factory.create.assert_awaited_once_with(
        bot_id="b1",
        user_id="owner-u",
    )
