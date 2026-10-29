"""Route-level contract: enablement success schedules the AGENTS.md sync, failure never does."""

import pytest

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.knowledge_management.router import router
from agentclaw.community.api.bot_knowledge_service import BotKnowledgeServiceProtocol
from agentclaw.community.core.bot_knowledge.agents_md_sync import (
    BotKnowledgeAgentsMdSync,
)
from agentclaw.community.core.knowledge_management.config import (
    KnowledgeManagementConfig,
)
from agentclaw.community.kernel.knowledge import BotKnowledgeScope, KnowledgeError
from agentclaw.community.utils.env_utils import get_current_env
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

PREFIX = "/api/knowledge/bots/bot/knowledge-bases/kb/enablement"
BODY = {"target_owner_id": "owner", "enabled": True, "expected_revision": 0}


class StubBotKnowledgeService:
    def __init__(self):
        self.calls = []
        self.error = None

    def set_enabled(self, *, scope, actor_id, kb_id, enabled, expected_revision):
        self.calls.append((scope, actor_id, kb_id, enabled, expected_revision))
        if self.error is not None:
            raise self.error
        return {
            "bot_id": scope.bot_id,
            "owner_id": scope.owner_id,
            "kb_id": kb_id,
            "enabled": enabled,
            "revision": 1,
        }


class StubAgentsMdSync:
    def __init__(self):
        self.calls = []

    async def sync(self, *, scope, actor_id):
        self.calls.append((scope, actor_id))


@pytest.fixture
def pair():
    service, sync = StubBotKnowledgeService(), StubAgentsMdSync()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="SSO", staffId="actor", operatorName="login"
    )
    injector = Injector(auto_bind=False)
    injector.binder.bind(BotKnowledgeServiceProtocol, to=service)
    injector.binder.bind(BotKnowledgeAgentsMdSync, to=sync)
    injector.binder.bind(
        KnowledgeManagementConfig, to=KnowledgeManagementConfig(True, "teamclaw")
    )
    attach_injector(app, injector)
    with TestClient(app) as client:
        yield client, service, sync


def test_enablement_success_schedules_the_agents_md_sync(pair):
    client, service, sync = pair
    response = client.put(PREFIX, json=BODY)
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert sync.calls == [
        (BotKnowledgeScope("bot", "owner", get_current_env()), "actor")
    ]


def test_enablement_failure_never_schedules_the_agents_md_sync(pair):
    client, service, sync = pair
    service.error = KnowledgeError("REVISION_CONFLICT", 409)
    response = client.put(PREFIX, json=BODY)
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "REVISION_CONFLICT"}}
    assert sync.calls == []
    assert len(service.calls) == 1
