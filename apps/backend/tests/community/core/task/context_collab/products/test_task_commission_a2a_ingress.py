"""Real HTTP/signature/DI/SQLite ingress; no live Bot runtime or gateway."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import secrets

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Binder, Injector

from agentclaw.community.adapters.http.task import commission_a2a_auth
from agentclaw.community.adapters.http.task.commission_a2a import router
from agentclaw.community.core.gateway_principal import PrincipalVerifierConfig
from agentclaw.community.core.task.context_collab.errors import ContextCollabValidationError
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import ActorContext
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import PrincipalRef
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import SpaceRef
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    PublishTaskCommand, TaskAdvanceCommand, TaskCommissionServiceProtocol,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ContextCollabServiceProtocol, EventAppendCommand,
)
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.di.modules.task_persistence_module import TaskPersistenceModule
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT

from tests.community.factories.task_commission import (
    BOT_ID, OWNER, commission_report_request as _request,
)
HUMAN = ActorContext(PrincipalRef("human", "u_publisher"), source="api")
BOT = ActorContext(PrincipalRef("bot", BOT_ID), source="a2a")
PATH = "/a2a/task-commission"


def _principal(bot_id=BOT_ID, tenant=DEFAULT_AVERNET_TENANT):
    return {"type": "bot", "tenant": tenant, "bot": {
        "bot_uuid": bot_id, "owner_id": OWNER, "app_id": 1,
        "agent_code": "writer", "tenant": tenant,
    }}


@pytest.fixture
def ingress(db, monkeypatch):
    def database(binder: Binder):
        binder.bind(DatabasePlugin, to=db)

    injector = Injector([TaskPersistenceModule(), TaskModule(), database])
    service = injector.get(TaskCommissionServiceProtocol)
    created = service.publish_task(PublishTaskCommand(
        title="安全报告", body="输出 Markdown", space=SpaceRef("space_1", "test"),
        assignee_agent_id=BOT_ID,
    ), HUMAN)
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, injector)
    key = secrets.token_hex(32)
    monkeypatch.setattr(commission_a2a_auth, "get_principal_verifier_config", lambda:
                        PrincipalVerifierConfig(key, "backend", "gateway"))

    def token(principals=None, **claims):
        now = datetime.now(timezone.utc)
        payload = {"iss": "gateway", "aud": "backend", "iat": now,
                   "exp": now + timedelta(seconds=60),
                   "principals": [_principal()] if principals is None else principals}
        payload.update(claims)
        return jwt.encode(payload, key, algorithm="HS256")

    with TestClient(app) as client:
        yield client, service, injector.get(ContextCollabServiceProtocol), created, token




def _operation(request):
    return request["params"]["message"]["parts"][0]["data"]


def _send(ingress, request, token=None):
    client, _, _, _, sign = ingress
    response = client.post(PATH, json=request, headers={"X-Avernet-Principal": token or sign()})
    assert response.status_code == 200, response.text
    return response.json()["result"]["parts"][0]["data"]


def _version(ingress):
    _, _, generic, created, _ = ingress
    return generic.get_status(created.collet_id, trunk_events=False, context=HUMAN).context_version


def test_signed_executor_reports_through_http_and_owner_accepts(ingress):
    _, service, _, created, _ = ingress
    assert created.context_version == 2
    for action, revision, payload, status in [
        ("start", 2, {}, "in_progress"),
        ("progress", 3, {"percent": 50, "note": "初稿完成"}, "in_progress"),
        ("submit", 4, {"artifacts": ["artifact://report"]}, "reviewing"),
    ]:
        result = _send(ingress, _request(created, action, revision, payload))
        assert result["status"] == "SUCCEEDED", result
        assert result["result_refs"][0]["version"] == str(revision + 1)
        detail = service.detail_task(created.collet_id)
        assert detail.status == status
        assert detail.events[-1]["actor_id"] == BOT_ID
    assert detail.events[-1]["payload"]["artifacts"] == ["artifact://report"]
    accepted = service.accept_task(TaskAdvanceCommand(created.collet_id), HUMAN)
    assert accepted.status == "done"


@pytest.mark.parametrize("case", [
    "missing", "tampered", "expired", "audience", "user", "app", "two-bots", "blank", "tenant",
])
def test_untrusted_identity_never_writes(ingress, case):
    client, _, _, created, sign = ingress
    principals = [_principal()]
    claims = {}
    if case == "expired":
        claims["exp"] = datetime.now(timezone.utc) - timedelta(seconds=60)
    elif case == "audience":
        claims["aud"] = "another-service"
    elif case == "user":
        principals = [{"type": "user", "subject": {"id": BOT_ID, "username": "writer"}}]
    elif case == "app":
        principals = [{"type": "app", "tenant": DEFAULT_AVERNET_TENANT, "app": {
            "app_id": 1, "app_name": "writer", "owners": OWNER, "tenant": DEFAULT_AVERNET_TENANT}}]
    elif case == "two-bots":
        principals.append(_principal("other"))
    elif case == "blank":
        principals[0]["bot"]["bot_uuid"] = " "
    elif case == "tenant":
        principals[0]["bot"]["tenant"] = "other"
    signed = sign(principals, **claims)
    if case == "tampered":
        signed = jwt.encode({"principals": principals}, secrets.token_hex(32), algorithm="HS256")
    headers = {"X-Avernet-Principal": signed} if case != "missing" else {}
    headers.update({"X-Bot-Id": BOT_ID, "X-Staff-Id": BOT_ID})
    response = client.post(PATH, json=_request(created), headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
    assert _version(ingress) == 2


@pytest.mark.parametrize("case,category", [
    ("caller", "AUTHORIZATION"), ("other-bot", "VALIDATION"),
    ("qualified-identity", "VALIDATION"), ("accountable", "AUTHORIZATION"),
    ("cross-tenant", "NOT_FOUND"), ("binding", "VALIDATION"), ("profile", "VALIDATION"),
    ("collet", "VALIDATION"), ("revision", "CONFLICT"), ("deadline", "VALIDATION"),
    ("bool-percent", "VALIDATION"), ("start-percent", "VALIDATION"),
    ("idempotency", "VALIDATION"), ("accept", "VALIDATION"),
])
def test_authenticated_invalid_report_never_writes(ingress, case, category):
    _, _, _, created, sign = ingress
    request = _request(created)
    op = _operation(request)
    ctx = op["context"]
    principals = [_principal()]
    if case == "caller":
        ctx["caller"]["identity_id"] = "other"
    elif case in {"other-bot", "qualified-identity"}:
        other = "other" if case == "other-bot" else "bot_writer_001:other-owner"
        principals[0]["bot"]["bot_uuid"] = other
        ctx["caller"]["identity_id"] = other
    elif case == "accountable":
        ctx["accountable_principal_ref"]["identity_id"] = HUMAN.actor.principal_id
    elif case == "cross-tenant":
        principals = [_principal(tenant="other")]
    elif case == "binding":
        ctx["method_binding_ref"]["object_id"] = "wrong"
    elif case == "profile":
        ctx["product_profile_ref"]["version"] = "2"
    elif case == "collet":
        ctx["collet_ref"]["context_id"] = "other"
    elif case == "revision":
        op["expected_revision"] = 1
    elif case == "deadline":
        ctx["deadline"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    elif case == "bool-percent":
        op.update(action="progress", payload={"percent": True})
    elif case == "start-percent":
        op["payload"] = {"percent": 50}
    elif case == "idempotency":
        ctx["idempotency_key"] = "key-1"
    elif case == "accept":
        op["action"] = "accept"
    result = _send(ingress, request, sign(principals))
    assert result["status"] == "REJECTED", result
    assert result["error"]["category"] == category, result
    assert _version(ingress) == 2


def test_stale_replay_does_not_append_second_progress_event(ingress):
    created = ingress[3]
    assert _send(ingress, _request(created))["status"] == "SUCCEEDED"
    request = _request(created, "progress", 3, {"percent": 50})
    assert _send(ingress, request)["status"] == "SUCCEEDED"
    result = _send(ingress, deepcopy(request))
    assert result["error"]["category"] == "CONFLICT"
    assert _version(ingress) == 4


@pytest.mark.parametrize("actor", [HUMAN, ActorContext(PrincipalRef("human", BOT_ID), "api"),
                                    ActorContext(PrincipalRef("bot", "other"), "api")])
def test_product_and_generic_append_cannot_bypass_progress_role(ingress, actor):
    _, service, generic, created, _ = ingress
    service.start_task(TaskAdvanceCommand(created.collet_id), BOT)
    with pytest.raises(ContextCollabValidationError):
        service.report_progress(TaskAdvanceCommand(created.collet_id, percent=88), actor)
    command = EventAppendCommand(3, "task.progress.updated", "Task", actor.actor,
                                 None, "public", {"percent": 88})
    with pytest.raises(ContextCollabValidationError):
        generic.append_event(created.collet_id, command, actor)
    assert _version(ingress) == 3


def test_terminal_task_refuses_report(ingress):
    _, service, _, created, _ = ingress
    service.cancel_task(TaskAdvanceCommand(created.collet_id), HUMAN)
    result = _send(ingress, _request(created, "start", 3))
    assert result["error"]["category"] == "VALIDATION"
    assert _version(ingress) == 3


def test_wire_errors_never_write(ingress):
    client, _, _, created, sign = ingress
    headers = {"X-Avernet-Principal": sign()}
    response = client.post(PATH, content="{", headers=headers)
    assert response.json()["error"]["code"] == -32700
    request = _request(created)
    request["method"] = "tasks/get"
    assert client.post(PATH, json=request, headers=headers).json()["error"]["code"] == -32601
    request = _request(created)
    request["params"]["message"]["role"] = "agent"
    assert client.post(PATH, json=request, headers=headers).json()["error"]["code"] == -32602
    assert client.post(PATH, content="x" * 65537, headers=headers).status_code == 413
    assert _version(ingress) == 2


def test_persistence_failure_is_not_a_success_receipt(ingress, engine):
    from sqlalchemy import event

    def reject_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT"):
            raise RuntimeError("storage unavailable")

    event.listen(engine, "before_cursor_execute", reject_insert)
    try:
        result = _send(ingress, _request(ingress[3]))
    finally:
        event.remove(engine, "before_cursor_execute", reject_insert)
    assert result["status"] == "FAILED"
    assert result["error"]["category"] == "INTERNAL"
    assert "storage unavailable" not in str(result)
    assert _version(ingress) == 2
