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
    bot_repo.get_by_id_and_owner.return_value = {
        "bot_id": "b1", "owner_id": "u1", "active_engine": "openclaw",
        "bot_type": "service", "status": "ACTIVE",
    }

    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = SimpleNamespace(provider="teclaw")

    skill_repo = MagicMock()
    skill_repo.get_by_id.return_value = {
        "id": "1", "name": "x", "link_name": "x", "git_path": "local://skills-local/x",
    }

    param_service = MagicMock()
    param_service.get_skill_parameters.return_value = {}
    param_service.save_skill_parameters = AsyncMock(return_value=False)
    param_factory = MagicMock()
    param_factory.create = AsyncMock(return_value=param_service)

    center_client = MagicMock()

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
