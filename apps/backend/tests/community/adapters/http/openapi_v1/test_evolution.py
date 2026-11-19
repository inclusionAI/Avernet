"""RSI uses the real public principal + collaborator gates before any relay."""

import json

import httpx
import pytest
from agentclaw.community.adapters.http.openapi_v1 import evolution
from agentclaw.community.adapters.http.openapi_v1.access_log import (
    PublicApiAccessLogMiddleware,
)
from agentclaw.community.adapters.http.openapi_v1.authorization_rules_evolution import (
    OPERATIONS,
)
from agentclaw.community.api.collaborator_lock_service import (
    CollaboratorLockServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.di.evolution_control_config import EvolutionControlConfig
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from agentclaw.community.utils.gateway_principal_config import (
    reset_principal_verifier_config_cache,
)
from avernet_evolution.adapters.backend_access import HEADER, BackendAccess, fingerprint
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider, Module

from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    public_router,
)
from tests.community.adapters.http.openapi_v1.test_bot_access import (
    BOT,
    CALLER,
    OWNER,
    _Audit,
    _Bots,
    _Collaborators,
    _Locks,
)
from tests.community.adapters.http.openapi_v1.test_principal_seam import (
    KEY,
    boot_with_key,
    mint,
)

RESOURCE = "stable-bot-entity"
URL = f"/openapi/v1/bots/{BOT}/evolution"


class Bots(_Bots):
    def list_matches_by_legacy_locator(self, bot_id, locator):
        if bot_id != BOT or locator not in {OWNER, RESOURCE}:
            return []
        return [
            {
                "id": 7,
                "bot_id": BOT,
                "entity_id": RESOURCE,
                "owner_id": OWNER,
                "active_engine": "openclaw",
                "bot_type": "personal",
                "env": "test",
            }
        ]


class Collaborators(_Collaborators):
    explicit_level = None

    def get_explicit_permission_level(self, *, bot, user_id, env=None):
        return self.level if self.explicit_level is None else self.explicit_level


@pytest.fixture
def surface(monkeypatch):
    boot_with_key(KEY)
    calls, audit = [], _Audit()
    collaborators = Collaborators(PermissionLevel.MEMBER)
    response = {
        "status": 200,
        "data": {},
        "headers": {"ETag": '"tag"', "Set-Cookie": "not-forwarded=1"},
    }

    async def upstream(req):
        access = BackendAccess(key=KEY, tenant=DEFAULT_AVERNET_TENANT).verify(
            req.headers[HEADER],
            bot_id=BOT,
            owner_hint=RESOURCE,
            request_hash=fingerprint(
                req.method,
                req.url.path,
                req.url.query.decode(),
                req.content,
                req.headers,
            ),
        )
        calls.append((req, access))
        if "exception" in response:
            raise response["exception"]
        return httpx.Response(
            response["status"],
            json=response.get(
                "envelope",
                {
                    "code": response["status"] * 1000,
                    "message": "fixture",
                    "data": response["data"],
                    "request_id": "upstream-trace",
                },
            ),
            headers=response["headers"],
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        evolution.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(upstream)
        ),
    )

    class Bindings(Module):
        def configure(self, binder):
            for interface, value in (
                (BotRepository, Bots()),
                (CollaboratorServiceProtocol, collaborators),
                (BotCollabLogRepositoryProtocol, audit),
                (CollaboratorLockServiceProtocol, _Locks()),
                (
                    EvolutionControlConfig,
                    EvolutionControlConfig(upstream="http://evolution.test"),
                ),
            ):
                binder.bind(interface, to=InstanceProvider(value))

    app = FastAPI()
    app.include_router(public_router())
    mount_public_error_handlers(app)
    attach_injector(app, Injector([Bindings()]))
    app.add_middleware(PublicApiAccessLogMiddleware)
    with TestClient(app) as client:
        yield client, calls, collaborators, response, audit
    reset_principal_verifier_config_cache()


def request(client, method="GET", suffix="/bindings", *, token=True, **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["X-Avernet-Principal"] = mint(user_id=CALLER, tenant=None)
    params = {"user_id": CALLER, "entity_id": RESOURCE} | kwargs.pop("params", {})
    return client.request(
        method, URL + suffix, headers=headers, params=params, **kwargs
    )


@pytest.mark.parametrize("method,suffix", OPERATIONS)
def test_all_operations_require_system_login_before_relay(surface, method, suffix):
    client, calls, *_ = surface
    result = request(client, method, suffix.replace("{run_id}", "run-1"), token=False)
    assert result.status_code == 401
    assert not calls


@pytest.mark.parametrize("method,suffix", OPERATIONS)
def test_all_operations_require_bot_permission(surface, method, suffix):
    client, calls, collaborators, *_ = surface
    collaborators.level = PermissionLevel.NONE
    result = request(client, method, suffix.replace("{run_id}", "run-1"))
    assert result.status_code == 404
    assert not calls


def test_collaborator_actor_owner_and_stable_entity_are_distinct(surface):
    client, calls, _, _response, _audit = surface
    result = request(
        client,
        headers={
            "Cookie": "IAM_TOKEN=not-forwarded",
            HEADER: "client-forgery",
            "Authorization": "Bearer not-forwarded",
        },
    )
    assert result.status_code == 200
    req, grant = calls[0]
    assert grant.actor == CALLER != grant.bot.owner_id
    assert grant.bot.owner_id == OWNER != grant.bot.entity_id
    assert grant.bot.entity_id == RESOURCE
    assert dict(req.url.params) == {"entity_id": RESOURCE}
    assert "cookie" not in req.headers and "authorization" not in req.headers
    assert req.headers[HEADER] != "client-forgery"
    assert result.headers["etag"] == '"tag"'
    assert "set-cookie" not in result.headers


@pytest.mark.parametrize(
    "params,status",
    [
        ({"user_id": OWNER}, 403),
        ({"entity_id": "other-bot-owner"}, 404),
        ({"entity_id": RESOURCE, "owner_id": "conflicting"}, 422),
    ],
)
def test_client_selectors_cannot_change_identity_or_scope(surface, params, status):
    client, calls, *_ = surface
    assert request(client, params=params).status_code == status
    assert not calls


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"binding_id": "binding", "user_id": OWNER},
        {"binding_id": "binding", "actor": OWNER},
    ],
)
def test_invalid_payload_is_not_forwarded_even_before_handler_validation(surface, body):
    client, calls, *_ = surface
    result = request(
        client, "POST", "/runs", json=body, headers={"Idempotency-Key": "attempt"}
    )
    assert result.status_code == 422
    assert not calls


def test_submit_keeps_actor_idempotency_body_and_audit(surface):
    client, calls, _, response, audit = surface
    response.update(status=202, data={"run": "run-fixture"})
    result = request(
        client,
        "POST",
        "/runs",
        json={"binding_id": "binding"},
        headers={"Idempotency-Key": "same-attempt"},
    )
    assert result.status_code == 202
    req, access = calls[0]
    assert req.headers["idempotency-key"] == "same-attempt"
    assert json.loads(req.content) == {"binding_id": "binding"}
    assert access.actor == CALLER
    assert audit.rows


@pytest.mark.parametrize("status", [403, 404, 409, 412, 428, 500])
def test_control_conflicts_and_failures_are_not_reported_as_success(surface, status):
    client, calls, _, response, audit = surface
    response["status"] = status
    result = request(
        client, "PUT", "/bindings", json={"bindings": []}, headers={"If-Match": '"old"'}
    )
    assert result.status_code == status
    assert calls[0][0].headers["if-match"] == '"old"'
    assert not audit.rows


def test_transport_timeout_is_unconfirmed_without_retry(surface):
    client, calls, _, response, _ = surface
    response["exception"] = httpx.ReadTimeout("sensitive transport detail")
    result = request(
        client,
        "POST",
        "/runs",
        json={"binding_id": "binding"},
        headers={"Idempotency-Key": "retry-same-key"},
    )
    assert result.status_code == 504
    assert len(calls) == 1
    assert "sensitive" not in result.text


def test_worker_endpoints_and_old_unscoped_catalog_are_not_relayed(surface):
    client, calls, *_ = surface
    for path in [URL + "/jobs/jobs:claim", "/openapi/v1/evolution/strategies"]:
        assert client.get(path).status_code in {401, 404, 405}
    assert not calls


@pytest.mark.parametrize(
    "upstream",
    ["http://user:password@host", "https://host/path", "https://host?query=1"],
)
def test_upstream_is_deployment_origin_not_user_controlled_url(upstream):
    with pytest.raises(ValueError):
        EvolutionControlConfig(upstream=upstream)


def test_space_chat_membership_does_not_bypass_existing_bot_operations_policy(surface):
    client, calls, collaborators, response, _ = surface
    # Shared system policy distinguishes Space read/chat from Bot operations.
    collaborators.explicit_level = PermissionLevel.NONE
    response.update(status=202, data={"run": "member-run"})
    result = request(
        client,
        "POST",
        "/runs",
        json={"binding_id": "binding"},
        headers={"Idempotency-Key": "member-attempt"},
    )
    assert result.status_code == 404
    assert not calls
    response.update(status=200, data={})
    assert request(client).status_code == 200  # still permitted to read


@pytest.mark.parametrize("envelope", [["unexpected"], {"code": 200}, {"code": 500000}])
def test_invalid_upstream_envelopes_fail_closed(surface, envelope):
    client, calls, _, response, _ = surface
    response["envelope"] = envelope
    result = request(client)
    assert result.status_code == 502
    assert len(calls) == 1
    assert "Invalid Evolution response" in result.text


def test_upstream_login_redirect_is_not_followed_or_forwarded(surface):
    client, calls, _, response, _ = surface
    response.update(status=302, headers={"Location": "https://login.example.test/"})
    result = request(client)
    assert result.status_code == 502
    assert len(calls) == 1
    assert "location" not in result.headers
    assert "login.example.test" not in result.text
