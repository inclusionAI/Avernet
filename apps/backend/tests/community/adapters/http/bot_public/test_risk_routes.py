from dataclasses import replace
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.bot_public.risk_routes import (
    legacy_router,
    openapi_router,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_public_risk_service import (
    BotPublicRiskServiceProtocol,
    BotPublicRiskQueryProtocol,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_public_risk.application import RiskApplication
from agentclaw.community.core.bot_public_risk.repository import BotPublicRiskRepository
from agentclaw.community.core.bot_public_risk.query import RiskQuery
from agentclaw.community.core.gateway_principal import (
    GatewayUser,
    UserPrincipal,
    VerifiedCaller,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
)


class Bots:
    def get_by_id_and_entity(self, bot_id, entity_id):
        if (bot_id, entity_id) != ("default", "entity"):
            return None
        return {
            "id": 5,
            "bot_id": bot_id,
            "entity_id": entity_id,
            "owner_id": "u1",
            "env": "dev",
        }

    def list_matches_by_legacy_locator(self, bot_id, owner_id):
        row = self.get_by_id_and_entity(bot_id, "entity")
        return [row] if row and owner_id == "u1" else []


class Collaborators:
    def get_explicit_permission_level(self, *, bot, user_id, env):
        return PermissionLevel.ADMIN if user_id == "manager" else PermissionLevel.NONE


class Records:
    def __init__(self):
        self.row = None

    def get_by_request(self, *, env, client_scope, client_request_id):
        if self.row and (
            self.row.env,
            self.row.client_scope,
            self.row.client_request_id,
        ) == (env, client_scope, client_request_id):
            return self.row
        return None

    def get_current(self, *, env, bot_pk, public_scope):
        if self.row and (self.row.env, self.row.bot_pk, self.row.public_scope) == (
            env,
            bot_pk,
            public_scope,
        ):
            return self.row
        return None

    def get(self, *, env, risk_check_id):
        if self.row and (self.row.env, self.row.risk_check_id) == (env, risk_check_id):
            return self.row
        return None


class Tasks:
    def __init__(self, repo):
        self.repo = repo
        self.polls = 0

    def start(self, record):
        self.repo.row = replace(record, current_target_key="current")
        return self.repo.row, True

    def poll(self, *, env, risk_check_id, manual):
        self.polls += 1
        return "UPDATED"


class Builder:
    def build(self, *, bot, target):
        return {"agent_info": {"agentCode": "code"}}, "a" * 64


def client_for(user="u1"):
    repo = Records()
    tasks = Tasks(repo)
    app_service = RiskApplication(repo, tasks, Builder())

    class Bindings(Module):
        def configure(self, binder):
            binder.bind(BotRepository, to=Bots())
            binder.bind(CollaboratorServiceProtocol, to=Collaborators())
            binder.bind(BotPublicRiskRepository, to=repo)
            binder.bind(BotPublicRiskQueryProtocol, to=RiskQuery(repo))
            binder.bind(BotPublicRiskServiceProtocol, to=app_service)

    app = FastAPI()
    app.include_router(openapi_router)
    app.include_router(legacy_router)
    app.dependency_overrides[require_principal] = lambda: VerifiedCaller(
        principals=(UserPrincipal(subject=GatewayUser(id=user, username=user)),)
    )
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=user, staffId=user, operatorName=user
    )
    attach_injector(app, Injector([Bindings()]))
    mount_public_error_handlers(app)
    return TestClient(app), repo, tasks


def test_openapi_lifecycle_uses_local_get_and_original_refresh():
    client, repo, tasks = client_for()
    url = "/openapi/v1/collaboration/bots/default:entity/public/risk-checks"
    assert (
        client.get(url + "/current?user_id=u1&public_scope=user").json()["data"] is None
    )
    body = {"client_request_id": "r1", "public_scope": "user", "visibility": "public"}
    response = client.post(url + "?user_id=u1", json=body)
    assert response.status_code == 202 and response.json()["code"] == 202000
    record_id = response.json()["data"]["risk_check_id"]
    assert repo.row.client_scope == "openapi:u1"
    assert client.post(url + "?user_id=u1", json=body).status_code == 200
    assert (
        client.get(url + "/current?user_id=u1&public_scope=user").json()["data"][
            "risk_check_id"
        ]
        == record_id
    )
    assert client.get(url + f"/{record_id}?user_id=u1").status_code == 200
    assert tasks.polls == 0
    assert (
        client.post(url + f"/{record_id}/refresh?user_id=u1").json()["data"]["outcome"]
        == "UPDATED"
    )
    assert tasks.polls == 1
    assert (
        client.post(
            url + "?user_id=u1", json={**body, "public_scope": "agent"}
        ).status_code
        == 409
    )


def test_cross_bot_and_mismatched_user_are_refused():
    client, _, _ = client_for()
    url = "/openapi/v1/collaboration/bots/default:entity/public/risk-checks"
    assert (
        client.get(url + "/current?user_id=other&public_scope=user").status_code == 403
    )
    assert (
        client.get(
            url.replace("default:entity", "default:other")
            + "/current?user_id=u1&public_scope=user"
        ).status_code
        == 404
    )
    assert (
        client_for("stranger")[0]
        .get(url + "/current?user_id=stranger&public_scope=user")
        .status_code
        == 404
    )


def test_legacy_current_and_submit_use_owner_locator_without_impersonation():
    client, repo, _ = client_for()
    url = "/api/bots/default/public/risk-checks"
    assert client.get(url + "/current").json()["data"] is None
    response = client.post(url, json={"client_request_id": "r2", "public": "1"})
    assert response.status_code == 200 and response.json()["success"]
    assert repo.row.client_scope == "legacy:u1" and repo.row.public_scope == "legacy"
    stranger, _, _ = client_for("stranger")
    assert stranger.get(url + "/current?owner_id=u1").status_code == 404


def test_injector_constructs_application_with_test_builder_and_security_provider():
    from injector import InstanceProvider
    from agentclaw.community.core.bot_public_risk.application import RiskInputBuilder
    from agentclaw.community.core.bot_public_risk.tasks import SecurityProvider
    from agentclaw.community.core.task_queue.services.task_queue_service import (
        TaskQueueService,
    )
    from agentclaw.community.di.modules.bot_public_module import BotPublicModule
    from agentclaw.community.di.modules.bot_public_risk_module import BotPublicRiskModule

    class Security:
        def submit_agent_scan(self, agent_info):
            return "check"

        def get_agent_check_report(self, check_id):
            raise AssertionError("not queried by submission")

    class Queue:
        def enqueue(self, *args, **kwargs):
            return None

    class ActualRepo(Records):
        def create_or_replay(self, record):
            self.row = replace(record, current_target_key="current")
            return self.row, True

    repo = ActualRepo()

    class TestBindings(Module):
        def configure(self, binder):
            binder.bind(BotPublicRiskRepository, to=InstanceProvider(repo))
            binder.bind(TaskQueueService, to=InstanceProvider(Queue()))
            binder.bind(RiskInputBuilder, to=InstanceProvider(Builder()))
            binder.bind(SecurityProvider, to=InstanceProvider(Security()))

    injector = Injector([BotPublicRiskModule(), BotPublicModule(), TestBindings()])
    service = injector.get(BotPublicRiskServiceProtocol)
    bot = SimpleNamespace(
        bot_pk=5, current_owner_id="u1", bot={"env": "dev", "owner_id": "u1"}
    )
    row, created = service.submit(
        bot=bot,
        actor_id="u1",
        surface="openapi",
        body={
            "client_request_id": "x",
            "public_scope": "user",
            "visibility": "public",
            "view_depts": [],
        },
    )
    assert created and row.client_scope == "openapi:u1"
