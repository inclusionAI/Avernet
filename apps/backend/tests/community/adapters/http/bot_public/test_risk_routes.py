from dataclasses import replace
from types import SimpleNamespace

import pytest

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
from agentclaw.community.core.bot_public_risk.repository_protocol import RiskRepository
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
            from injector import InstanceProvider
            binder.bind(RiskRepository, to=InstanceProvider(repo))
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
    from agentclaw.community.core.bot_public_risk.tasks import SecurityProvider, SecurityReport, SecuritySubmission
    from agentclaw.community.core.task_queue.services.task_queue_service import (
        TaskQueueService,
    )
    from agentclaw.community.di.modules.bot_public_module import BotPublicModule
    from agentclaw.community.di.modules.bot_public_risk_module import (
        BotPublicRiskModule,
    )

    submissions = []
    queued = []

    class Security:
        def submit_agent_scan(self, agent_info):
            submissions.append(agent_info)
            return SecuritySubmission("check", SecurityReport("DETECTING", "https://example.test/check"))

        def get_agent_check_report(self, check_id):
            raise AssertionError("not queried by submission")

    class Queue:
        def enqueue(self, kind, *args, **kwargs):
            queued.append(kind)

    from agentclaw.community.core.bot_public_risk.repository import BotPublicRiskRepository
    from tests.community.core.bot_public.test_risk_tasks import Db

    repo = BotPublicRiskRepository(Db())

    class TestBindings(Module):
        def configure(self, binder):
            binder.bind(RiskRepository, to=InstanceProvider(repo))
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
    assert row.phase == "CHECKING" and row.security_status == "DETECTING"
    assert row.check_id == "check" and row.detail_url == "https://example.test/check"
    assert repo.get(env="dev", risk_check_id=row.risk_check_id).detail_url == row.detail_url
    replay, replay_created = service.submit(
        bot=bot, actor_id="u1", surface="openapi",
        body={"client_request_id": "x", "public_scope": "user", "visibility": "public", "view_depts": []},
    )
    assert not replay_created and replay.risk_check_id == row.risk_check_id
    assert len(submissions) == 1 and queued == ["bot_public_risk_detecting"]


def test_authorized_other_bot_cannot_read_or_refresh_record():
    class TwoBots(Bots):
        def get_by_id_and_entity(self, bot_id, entity_id):
            if (bot_id, entity_id) == ("other", "entity"):
                return {
                    "id": 6,
                    "bot_id": bot_id,
                    "entity_id": entity_id,
                    "owner_id": "u1",
                    "env": "dev",
                }
            return super().get_by_id_and_entity(bot_id, entity_id)

    client, _, tasks = client_for()
    client.app.state.injector.binder.bind(BotRepository, to=TwoBots())
    base = "/openapi/v1/collaboration/bots/default:entity/public/risk-checks"
    created = client.post(
        base + "?user_id=u1",
        json={
            "client_request_id": "record",
            "public_scope": "user",
            "visibility": "public",
        },
    )
    record_id = created.json()["data"]["risk_check_id"]
    other = (
        f"/openapi/v1/collaboration/bots/other:entity/public/risk-checks/{record_id}"
    )
    assert client.get(other + "?user_id=u1").status_code == 404
    assert client.post(other + "/refresh?user_id=u1").status_code == 404
    assert tasks.polls == 0


def test_risk_operations_are_enforced_by_collaboration_check_gate():
    from agentclaw.community.adapters.http.openapi_v1.authorization_rules import (
        AUTHORIZATION,
        Check,
    )
    from agentclaw.community.core.bot_collaborator.models import PermissionLevel

    base = "/openapi/v1/collaboration/bots/{bot_id}/public/risk-checks"
    for method, suffix in (
        ("POST", ""),
        ("GET", "/current"),
        ("GET", "/{risk_check_id}"),
        ("POST", "/{risk_check_id}/refresh"),
    ):
        rule = AUTHORIZATION[(method, base + suffix)]
        assert isinstance(rule, Check)
        assert (
            rule.level == PermissionLevel.ADMIN
            and rule.explicit
        )


def test_collaboration_gate_refuses_stranger_before_handler(monkeypatch):
    from agentclaw.community.adapters.http.bot_public import risk_routes

    reached = []

    def bypass_handler(*args, **kwargs):
        reached.append(True)
        raise AssertionError("handler must not run")

    monkeypatch.setattr(risk_routes, "_authorize", bypass_handler)
    client, _, _ = client_for("stranger")
    response = client.get(
        "/openapi/v1/collaboration/bots/default:entity/public/risk-checks/current?user_id=stranger&public_scope=user"
    )
    assert response.status_code == 404
    assert not reached


def test_owner_handoff_allows_reads_but_refuses_submit_and_refresh():
    class FrozenBots(Bots):
        def get_by_id_and_entity(self, bot_id, entity_id):
            bot = super().get_by_id_and_entity(bot_id, entity_id)
            if bot:
                return {**bot, "ext": {"owner_handoff": {"status": "IN_PROGRESS"}}}
            return None

    client, _, tasks = client_for()
    base = "/openapi/v1/collaboration/bots/default:entity/public/risk-checks"
    body = {
        "client_request_id": "record",
        "public_scope": "user",
        "visibility": "public",
    }
    record_id = client.post(base + "?user_id=u1", json=body).json()["data"][
        "risk_check_id"
    ]
    client.app.state.injector.binder.bind(BotRepository, to=FrozenBots())
    assert client.get(base + "/current?user_id=u1&public_scope=user").status_code == 200
    assert client.get(base + f"/{record_id}?user_id=u1").status_code == 200
    assert (
        client.post(
            base + "?user_id=u1", json={**body, "client_request_id": "new"}
        ).status_code
        == 404
    )
    assert client.post(base + f"/{record_id}/refresh?user_id=u1").status_code == 404
    assert tasks.polls == 0


def test_legacy_owner_handoff_allows_reads_and_refuses_submit():
    class FrozenBots(Bots):
        def get_by_id_and_entity(self, bot_id, entity_id):
            bot = super().get_by_id_and_entity(bot_id, entity_id)
            return (
                {**bot, "ext": {"owner_handoff": {"status": "IN_PROGRESS"}}}
                if bot
                else None
            )

    client, repo, tasks = client_for()
    base = "/api/bots/default/public/risk-checks"
    record_id = client.post(
        base, json={"client_request_id": "first", "public": "1"}
    ).json()["data"]["risk_check_id"]
    client.app.state.injector.binder.bind(BotRepository, to=FrozenBots())
    assert client.get(base + "/current").status_code == 200
    assert (
        client.post(base, json={"client_request_id": "r", "public": "1"}).status_code
        == 404
    )
    assert client.post(base + f"/{record_id}/refresh").status_code == 404
    assert repo.row.risk_check_id == record_id and tasks.polls == 0


@pytest.mark.asyncio
async def test_openapi_refresh_keeps_event_loop_responsive(monkeypatch):
    import asyncio
    import time
    from starlette.requests import Request
    from agentclaw.community.adapters.http.bot_public import risk_routes
    from agentclaw.community.adapters.http.bot_public.schemas_risk import (
        RiskRecordResponse,
    )

    async def authorized(*args, **kwargs):
        return SimpleNamespace(bot_pk=5)

    async def record(*args, **kwargs):
        return object()

    class SlowService:
        def refresh(self, **kwargs):
            time.sleep(0.3)
            return "NOT_QUERIED", object()

    monkeypatch.setattr(risk_routes, "_authorize", authorized)
    monkeypatch.setattr(risk_routes, "_record", record)
    monkeypatch.setattr(
        risk_routes,
        "_view",
        lambda _: RiskRecordResponse(
            risk_check_id="r",
            phase="CHECKING",
            target={"public_scope": "user", "visibility": "public"},
        ),
    )
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    task = asyncio.create_task(
        risk_routes.refresh_openapi(
            bot_id="b:e",
            risk_check_id="r",
            actor_id="owner",
            request=request,
            bot=SimpleNamespace(bot_pk=5),
            query=None,
            service=SlowService(),
        )
    )
    await asyncio.sleep(0.03)
    assert not task.done(), (
        "refresh blocked the event loop until the external call completed"
    )
    result = await task
    assert result.data.outcome == "NOT_QUERIED"


def test_risk_openapi_uses_standard_verified_user_policy():
    from agentclaw.community.adapters.http.openapi_v1.principal import resolve_user_id_bypass
    client, repo, tasks = client_for(user='attacker')
    client.app.dependency_overrides[resolve_user_id_bypass] = lambda: frozenset()
    base = '/openapi/v1/collaboration/bots/default:entity/public/risk-checks'
    body = {'client_request_id': 'r1', 'public_scope': 'user', 'visibility': 'public'}
    responses = [client.post(base + '?user_id=u1', json=body),
                 client.get(base + '/current?user_id=u1&public_scope=user'),
                 client.get(base + '/risk-1?user_id=u1'),
                 client.post(base + '/risk-1/refresh?user_id=u1')]
    assert [r.status_code for r in responses] == [403, 403, 403, 403]
    assert repo.row is None and tasks.polls == 0


def test_bypass_does_not_change_correct_applicant_identity():
    from agentclaw.community.adapters.http.openapi_v1.principal import resolve_user_id_bypass
    client, repo, _ = client_for(user='u1')
    client.app.dependency_overrides[resolve_user_id_bypass] = lambda: frozenset({'u1'})
    base = '/openapi/v1/collaboration/bots/default:entity/public/risk-checks'
    result = client.post(base + '?user_id=u1', json={'client_request_id': 'r1', 'public_scope': 'user', 'visibility': 'public'})
    assert result.status_code == 202
    assert repo.row.operator_id == 'u1' and repo.row.client_scope == 'openapi:u1'


class AppGrants:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.calls = []

    def find(self, **scope):
        self.calls.append(scope)
        return object() if self.enabled and scope == {
            "bot_id": "default", "owner_id": "entity", "user_id": "manager", "app_id": 5,
        } else None


def app_client(grants=None, user="manager"):
    from agentclaw.community.core.gateway_principal import AppPrincipal, GatewayApp
    from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
    from injector import InstanceProvider
    client, repo, tasks = client_for(user=user)
    principal = VerifiedCaller(principals=(AppPrincipal(tenant="teamclaw", app=GatewayApp(
        app_id=5, app_name="integration", owners="unrelated-app-owner", tenant="teamclaw",
    )),))
    client.app.dependency_overrides[require_principal] = lambda: principal
    if grants is not None:
        client.app.state.injector.binder.bind(BotAppGrantServiceProtocol, to=InstanceProvider(grants))
    return client, repo, tasks


def test_delegated_application_uses_canonical_bot_grant_and_actual_operator():
    grants = AppGrants()
    client, repo, tasks = app_client(grants)
    base = "/openapi/v1/collaboration/bots/default:entity/public/risk-checks"
    body = {"client_request_id": "app-r1", "public_scope": "user", "visibility": "public"}
    result = client.post(base + "?user_id=manager", json=body)
    assert result.status_code == 202, result.text
    record_id = result.json()["data"]["risk_check_id"]
    assert repo.row.operator_id == "manager" and repo.row.owner_id == "u1"
    assert client.post(base + "?user_id=manager", json=body).status_code == 200
    assert client.get(base + "/current?user_id=manager&public_scope=user").status_code == 200
    assert client.get(base + "/" + record_id + "?user_id=manager").status_code == 200
    assert client.post(base + "/" + record_id + "/refresh?user_id=manager").status_code == 200
    assert tasks.polls == 1
    assert all(call["bot_id"] == "default" and call["owner_id"] == "entity" for call in grants.calls)
    grants.enabled = False
    assert client.get(base + "/" + record_id + "?user_id=manager").status_code == 404
    assert client.post(base + "/" + record_id + "/refresh?user_id=manager").status_code == 404
    assert tasks.polls == 1


@pytest.mark.parametrize("grants", [None, AppGrants(False)])
def test_application_without_bot_delegation_is_denied(grants):
    client, repo, tasks = app_client(grants)
    base = "/openapi/v1/collaboration/bots/default:entity/public/risk-checks"
    result = client.post(base + "?user_id=manager", json={"client_request_id":"r", "public_scope":"user", "visibility":"public"})
    assert result.status_code == 404
    assert repo.row is None and tasks.polls == 0


def test_bot_grant_does_not_replace_live_collaborator_permission():
    grants = AppGrants()
    client, repo, tasks = app_client(grants)
    class RevokedCollaborators:
        def get_explicit_permission_level(self, **kwargs):
            return PermissionLevel.NONE
    client.app.state.injector.binder.bind(CollaboratorServiceProtocol, to=RevokedCollaborators())
    response = client.post("/openapi/v1/collaboration/bots/default:entity/public/risk-checks?user_id=manager", json={"client_request_id":"r", "public_scope":"user", "visibility":"public"})
    assert response.status_code == 404 and repo.row is None


def test_application_cannot_reuse_grant_for_another_entity_or_user():
    for address, user in [("default:other", "manager"), ("default:entity", "stranger")]:
        client, repo, _ = app_client(AppGrants(), user=user)
        class TwoEntities(Bots):
            def get_by_id_and_entity(self, bot_id, entity_id):
                row = super().get_by_id_and_entity(bot_id, "entity")
                return {**row, "id": 6, "entity_id": "other"} if row and entity_id == "other" else super().get_by_id_and_entity(bot_id, entity_id)
        client.app.state.injector.binder.bind(BotRepository, to=TwoEntities())
        response = client.post(f"/openapi/v1/collaboration/bots/{address}/public/risk-checks?user_id={user}", json={"client_request_id":"r", "public_scope":"user", "visibility":"public"})
        assert response.status_code == 404 and repo.row is None


def test_other_openapi_user_id_bypass_contract_remains_available():
    import asyncio
    from agentclaw.community.adapters.http.openapi_v1.principal import require_user_id
    principal = VerifiedCaller(principals=(UserPrincipal(subject=GatewayUser(id='real', username='real')),))
    assert asyncio.run(require_user_id(principal, 'u1', frozenset({'u1'}))) == 'u1'


def test_response_exposes_persisted_invalidation_reason_without_internal_workflow():
    from agentclaw.community.adapters.http.bot_public.risk_routes import _view
    from agentclaw.community.core.bot_public_risk.repository import RiskCheck
    row = RiskCheck(risk_check_id="a", env="dev", bot_pk=5, owner_id="u1", operator_id="u1",
        client_scope="legacy:u1", client_request_id="req", public_scope="legacy",
        target_json='{"public_scope":"legacy","visibility":"PUBLIC","view_depts":[]}',
        request_hash="r", input_json="{}", input_hash="h", approval_biz_id="risk-a", phase="CLOSED",
        workflow_json='{"invalidation_reason":"REPLACED","internal_detail":"private"}')
    data = _view(row).model_dump()
    assert data["invalidation_reason"] == "REPLACED" and data["phase"] == "CLOSED"
    assert "internal_detail" not in data


def test_resolved_bot_context_is_shared_by_gate_and_handler():
    class CountingBots(Bots):
        def __init__(self):
            self.reads = 0
        def get_by_id_and_entity(self, bot_id, entity_id):
            self.reads += 1
            return super().get_by_id_and_entity(bot_id, entity_id)
    bots = CountingBots()
    client, _, _ = client_for()
    client.app.state.injector.binder.bind(BotRepository, to=bots)
    result = client.post("/openapi/v1/collaboration/bots/default:entity/public/risk-checks?user_id=u1", json={"client_request_id":"once", "public_scope":"user", "visibility":"public"})
    assert result.status_code == 202
    assert bots.reads == 1
