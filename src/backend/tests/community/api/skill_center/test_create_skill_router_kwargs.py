"""Regression: POST /api/skills must call SkillService.create_skill with the
`skill_path` kwarg, not `git_path`.

The service signature names the param `skill_path` (it stores it into the
`git_path` DB column). The request model field is `git_path`. The router used
to forward it as `git_path=` → TypeError → 500. We pin the contract with a
service stub whose signature mirrors the real one, so a wrong kwarg fails loudly.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.skill_center.skills import router as skills_router
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.adapters.http.dependencies import get_request_context


class _StrictService:
    """Mirrors SkillService.create_skill's real signature: passing git_path= raises."""

    def create_skill(self, name, description, skill_path, category="general",
                     tags=None, input_schema="", output_schema="",
                     is_public=False, user_id=None, bolt_id=None):
        return {"id": 1, "name": name, "git_path": skill_path, "tags": "[]",
                "is_public": False, "is_builtin": False}


class _Factory:
    def create(
        self,
        *,
        repo_dir=None,
        entity_id=None,
        bot_id=None,
        engine_type=None,
    ):
        return _StrictService()


def _client():
    app = FastAPI()
    app.include_router(skills_router)
    app.dependency_overrides[get_request_context] = lambda: SimpleNamespace(
        user_id="default", bot_id="default"
    )
    bot_repo = MagicMock(spec=BotRepository)
    bot_repo.get_by_id_and_owner.return_value = None
    path_factory = MagicMock(spec=WorkspacePathFactory)
    path_factory.get_bot_skills_repo_dir.return_value = "/tmp/skills-repo"

    class _M(Module):
        def configure(self, binder):
            binder.bind(SkillServiceFactoryProtocol, to=_Factory())
            binder.bind(BotRepository, to=bot_repo)
            binder.bind(WorkspacePathFactory, to=path_factory)

    attach_injector(app, Injector([_M()]))
    return TestClient(app, raise_server_exceptions=False)


def test_create_skill_forwards_skill_path_not_git_path():
    resp = _client().post("/api/skills", json={
        "name": "brainstorming",
        "git_path": "git://infra/common/brainstorming",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["git_path"] == "git://infra/common/brainstorming"
