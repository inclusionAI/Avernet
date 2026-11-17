"""Independent HTTP/real-service regression for administrative ledger correction."""
from types import SimpleNamespace

import pytest
from agentclaw.community.adapters.http.openapi_v1.bots import caller_token_expiry
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.principal import (
    resolve_user_id_bypass,
)
from agentclaw.community.api.caller_expiry_correction_service import (
    CallerExpiryCorrectionServiceProtocol,
)
from agentclaw.community.core.caller_identity.expiry_correction import (
    CallerExpiryCorrectionService,
)
from agentclaw.community.core.gateway_principal import (
    AppPrincipal,
    GatewayApp,
    GatewayUser,
    UserPrincipal,
    VerifiedCaller,
)
from agentclaw.community.utils.env_utils import get_current_env
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
)

PATH = "/openapi/v1/bots/target-bot/caller-instances/target-caller/token-expiry"


class RecordingInstances:
    def __init__(self):
        self.calls = []
        self.failure = None

    def correct_caller_token_expiry(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure:
            raise self.failure
        return {"instance_id": 19, "previous_expires_at": None}


class TargetBots:
    def get_by_id_and_entity(self, bot_id, entity_id):
        assert (bot_id, entity_id) == ("target-bot", "stable-entity")
        return {"entity_id": entity_id, "owner_id": "new-human-owner", "bot_type": "service",
                "call_type": "caller", "active_engine": "openclaw"}


class TargetBindings:
    def get_by_id(self, binding_id):
        assert binding_id == 27
        return SimpleNamespace(status="ACTIVE", env=get_current_env(), entity_id="stable-entity",
                               applied_by="target-caller", entity_type="staff",
                               apply_reason="caller_instance:target-bot", device_provider="arca")


def human(actor):
    return VerifiedCaller(principals=(UserPrincipal(subject=GatewayUser(id=actor, username=actor)),))


@pytest.fixture
def correction_client(monkeypatch):
    monkeypatch.setattr("agentclaw.community.core.caller_identity.expiry_correction.admin_scopes.super_admin",
                        lambda: frozenset({"verified-admin"}))
    instances = RecordingInstances()
    service = CallerExpiryCorrectionService(bots=TargetBots(), bindings=TargetBindings(), instances=instances)
    app = mount_public_error_handlers(FastAPI())
    app.include_router(caller_token_expiry.router)
    injector = Injector()
    injector.binder.bind(CallerExpiryCorrectionServiceProtocol, to=lambda: service)
    attach_injector(app, injector)
    app.dependency_overrides[resolve_user_id_bypass] = lambda: frozenset()

    def build(principal):
        app.dependency_overrides[require_principal] = lambda: principal
        return TestClient(app)

    return build, instances


def body():
    return {"entity_id": "stable-entity", "binding_id": 27,
            "expires_at": "2000-01-01T08:00:00+08:00", "expected_expires_at": None,
            "reason": "controlled refresh validation"}


def test_real_service_uses_verified_admin_and_exact_target(correction_client, caplog):
    build, instances = correction_client
    caplog.set_level("INFO")
    response = build(human("verified-admin")).patch(PATH, params={"user_id": "verified-admin"}, json=body())
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["instance_id"] == 19
    assert data["expires_at"] == "2000-01-01T00:00:00Z"
    assert data["previous_expires_at"] is None
    assert instances.calls[0]["owner_id"] == "stable-entity"
    assert instances.calls[0]["user_id"] == "target-caller"
    assert "caller_expiry_correction_requested" in caplog.text
    assert "caller_expiry_correction_succeeded" in caplog.text
    assert "new-human-owner" not in str(instances.calls)


@pytest.mark.parametrize("actor,query_actor,status", [
    ("ordinary", "ordinary", 403),
    ("ordinary", "verified-admin", 403),
    ("verified-admin", "target-caller", 403),
])
def test_actor_cannot_be_selected_by_query_or_target(correction_client, actor, query_actor, status):
    build, instances = correction_client
    response = build(human(actor)).patch(PATH, params={"user_id": query_actor}, json=body())
    assert response.status_code == status
    assert not instances.calls


def test_application_cannot_name_admin_to_gain_permission(correction_client):
    build, instances = correction_client
    principal = VerifiedCaller(principals=(AppPrincipal(tenant="teamclaw", app=GatewayApp(
        app_id=42, app_name="application", owners="app-owner", tenant="teamclaw")),))
    response = build(principal).patch(PATH, params={"user_id": "verified-admin"}, json=body())
    assert response.status_code == 401
    assert not instances.calls


@pytest.mark.parametrize("mutation", ["missing_expected", "naive_new", "naive_old", "spoof_body_actor"])
def test_schema_rejects_ambiguous_correction_before_repository(correction_client, mutation):
    build, instances = correction_client
    payload = body()
    if mutation == "missing_expected":
        del payload["expected_expires_at"]
    elif mutation == "naive_new":
        payload["expires_at"] = "2000-01-01T00:00:00"
    elif mutation == "naive_old":
        payload["expected_expires_at"] = "2000-01-01T00:00:00"
    else:
        payload["actor_id"] = "verified-admin"
    response = build(human("verified-admin")).patch(PATH, params={"user_id": "verified-admin"}, json=payload)
    assert response.status_code == 422
    assert not instances.calls


def test_stale_cas_has_conflict_response_and_failure_log(correction_client, caplog):
    build, instances = correction_client
    instances.failure = ValueError("caller_token_expiry_conflict")
    caplog.set_level("INFO")
    response = build(human("verified-admin")).patch(PATH, params={"user_id": "verified-admin"}, json=body())
    assert response.status_code == 409
    assert "caller_token_expiry_conflict" in response.text
    assert "caller_expiry_correction_failed" in caplog.text
    assert "caller_expiry_correction_succeeded" not in caplog.text


@pytest.mark.parametrize("failure", [RuntimeError("secret-token=runtime-sensitive"),
                                    ValueError("secret-token=unknown-value")])
def test_unexpected_backend_errors_never_expose_exception_text(correction_client, caplog, failure):
    build, instances = correction_client
    instances.failure = failure
    caplog.set_level("INFO")
    response = build(human("verified-admin")).patch(PATH, params={"user_id": "verified-admin"}, json=body())
    assert response.status_code == 500
    assert "secret-token" not in response.text
    assert "secret-token" not in caplog.text
    assert "caller_expiry_correction_failed" in caplog.text
    assert "caller_expiry_correction_succeeded" not in caplog.text
