"""Real signature verification + trusted tenant + real engine/SQL progress ingress."""

import secrets
from time import time

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.app import _domain_error_handler
from agentclaw.community.core.errors import DomainError
from agentclaw.community.adapters.http.task import commission_a2a_auth
from agentclaw.community.adapters.http.task.product_progress import router
from agentclaw.community.api.task.rollout_progress_service import (
    RolloutProgressServiceProtocol,
)
from agentclaw.community.core.gateway_principal import PrincipalVerifierConfig
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT

PATH = "/api/v1/collaboration/task/product-progress"


@pytest.fixture
def progress_client(runtime, monkeypatch):
    key = secrets.token_hex(32)
    monkeypatch.setattr(
        commission_a2a_auth,
        "get_principal_verifier_config",
        lambda: PrincipalVerifierConfig(key, "backend", "gateway"),
    )
    injector = Injector(auto_bind=False)
    injector.binder.bind(RolloutProgressServiceProtocol, to=runtime.engine)
    app = FastAPI()
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.include_router(router)
    attach_injector(app, injector)

    def headers(bot_id="worker-a", tenant=DEFAULT_AVERNET_TENANT, *, signing_key=key):
        now = int(time())
        claims = {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 300,
            "principals": [
                {
                    "type": "bot",
                    "tenant": tenant,
                    "bot": {
                        "bot_uuid": bot_id,
                        "owner_id": "actual-owner",
                        "tenant": tenant,
                        "app_id": 1,
                        "agent_code": "executor",
                    },
                }
            ],
        }
        token = jwt.encode(claims, signing_key, algorithm="HS256")
        return {"X-Avernet-Principal": token}

    with TestClient(app) as client:
        yield client, headers


async def running_report(h):
    await h.engine.advance(h.collet.id, h.owner)
    await h.engine.advance(h.collet.id, h.owner)
    snapshot = h.repository.get_collet(h.collet.id)
    stage = snapshot.current().stages[0]
    record = next(r for r in snapshot.records if r.stage_id == stage.stage_rollout_id)
    return {
        "request_id": "signed-report",
        "attempt": record.attempt,
        "expected_revision": snapshot.revision,
        "event": {
            "type": "RUN_FAILED",
            "stage_rollout_id": stage.stage_rollout_id,
            "artifacts": [],
            "acceptances": [],
            "error_msg": "execution failed",
        },
    }


@pytest.mark.asyncio
async def test_signed_failure_commits_and_replays_original_receipt(
    created, progress_client
):
    h = created
    client, headers = progress_client
    body = await running_report(h)
    response = client.post(PATH, json=body, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["data"]["accepted"] is True
    assert client.post(PATH, json=body, headers=headers()).json() == response.json()
    stored = h.repository.get_collet(h.collet.id)
    assert stored.collet.status.value == "RUN_FAILED"
    assert stored.current().stages[0].error_msg == "execution failed"
    assert not h.evidence.verify.called


@pytest.mark.asyncio
async def test_signed_identity_tenant_and_attempt_are_not_body_authority(
    created, progress_client
):
    h = created
    client, headers = progress_client
    body = await running_report(h)
    for kwargs, expected in (
        ({"headers": headers("another-bot")}, 403),
        ({"headers": headers(tenant="other-tenant")}, 404),
        ({"headers": headers(signing_key=secrets.token_hex(32))}, 401),
        ({"headers": {"x-user-id": "profile-owner"}}, 401),
    ):
        response = client.post(PATH, json=body, **kwargs)
        assert response.status_code == expected, response.text
    bad = client.post(PATH, json={**body, "attempt": "forged"}, headers=headers())
    assert bad.status_code == 409
    assert h.repository.get_collet(h.collet.id).revision == body["expected_revision"]
    assert not h.evidence.verify.called


@pytest.mark.asyncio
async def test_signed_success_still_needs_trusted_evidence_verifier(
    created, progress_client
):
    h = created
    client, headers = progress_client
    body = await running_report(h)
    h.state["value"] = "final"
    await h.engine.advance(h.collet.id, h.owner)
    body["expected_revision"] = h.repository.get_collet(h.collet.id).revision
    body["event"].update(
        type="ACCEPTANCE_SUCCEED",
        error_msg="",
        artifacts=[
            {
                "type": "document",
                "name": "design",
                "content": {"text": "reviewed design"},
            }
        ],
        acceptances=[
            {"condition": "design is complete", "passed": True, "reason": "verified"}
        ],
    )
    denied = client.post(PATH, json=body, headers=headers())
    assert denied.status_code == 403
    h.evidence.owner = PrincipalRef("bot", "validator-bot")
    response = client.post(PATH, json=body, headers=headers("validator-bot"))
    assert response.status_code == 200, response.text
    assert h.evidence.verify.call_args.args[-1].actor == PrincipalRef(
        "bot", "validator-bot"
    )
    assert (
        h.repository.get_collet(h.collet.id).current().stages[0].status.value
        == "ACCEPTANCE_SUCCEED"
    )


@pytest.mark.asyncio
async def test_progress_rejects_body_actor_tenant_and_boolean_revision(
    created, progress_client
):
    h = created
    client, headers = progress_client
    body = await running_report(h)
    for changed in (
        {**body, "actor": {"type": "human", "id": "profile-owner"}},
        {**body, "tenant": DEFAULT_AVERNET_TENANT},
        {**body, "expected_revision": True},
        {**body, "event": {**body["event"], "actor": "worker-a"}},
    ):
        assert client.post(PATH, json=changed, headers=headers()).status_code == 422
    assert h.repository.get_collet(h.collet.id).revision == body["expected_revision"]


def test_progress_bounds_body_before_json_parsing_or_execution(
    progress_client, runtime
):
    from agentclaw.community.adapters.http.task.product_body_limits import (
        MAX_PRODUCT_BODY_BYTES,
    )

    client, headers = progress_client
    response = client.post(
        PATH,
        content=b"x" * (MAX_PRODUCT_BODY_BYTES + 1),
        headers={**headers(), "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert not runtime.evidence.verify.called and not runtime.calls


def test_progress_bounds_streamed_body_without_content_length(progress_client, runtime):
    from agentclaw.community.adapters.http.task.product_body_limits import (
        MAX_PRODUCT_BODY_BYTES,
    )

    client, headers = progress_client
    chunks = (b"x" * (MAX_PRODUCT_BODY_BYTES // 2) for _ in range(3))
    response = client.post(
        PATH, content=chunks, headers={**headers(), "Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert not runtime.evidence.verify.called and not runtime.calls
