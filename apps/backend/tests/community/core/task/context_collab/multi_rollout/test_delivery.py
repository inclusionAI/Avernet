import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.app import _domain_error_handler
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.task.product_profiles import router
from agentclaw.community.api.task.product_profile_service import (
    ProductProfileServiceProtocol,
)
from agentclaw.community.core.errors import DomainError
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_contracts import (
    MultiRolloutStrategyPort,
    MultiRolloutIsolationPort,
    MultiRolloutNotificationPort,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    encode_composition,
)
from agentclaw.community.di.modules.task.product_profile_runtime import (
    ProductProfileRuntimeModule,
)
from .conftest import policy, composition, STAGE


@pytest.fixture
def client(multi):
    injector = Injector(auto_bind=False)
    injector.binder.bind(ProductProfileServiceProtocol, to=multi.service)
    app = FastAPI()
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        "test", "profile-owner", "test"
    )
    attach_injector(app, injector)
    with TestClient(app) as client:
        yield client


def test_http_multi_opt_in_schema_metadata_and_historical_session_query(client, multi):
    base = "/api/v1/collaboration/task/products"
    response = client.post(
        base + "/profiles",
        json=dict(
            name="http-multi",
            composition=encode_composition(composition(policy())),
            stages=STAGE,
        ),
    )
    assert response.status_code == 200, response.text
    profile = response.json()["data"]
    response = client.post(
        base + "/collets", json=dict(profile_id=profile["id"], content="build")
    )
    assert response.status_code == 200, response.text
    cid = response.json()["data"]["id"]
    response = client.get(f"{base}/collets/{cid}/rollouts")
    data = response.json()["data"]
    assert response.status_code == 200, response.text
    assert len(data["rollouts"]) == 4
    assert data["multi_rollout_control"]["phase"] == "CREATED"
    assert data["multi_rollout_control"]["configured"] == 4
    assert data["current_rollout_id"] == data["rollouts"][0]["rollout_id"]
    sid = data["rollouts"][-1]["stages"][0]["stage_rollout_id"]
    assert client.get(f"{base}/stages/{sid}/messages").json()["data"]["bound"] is False
    schema = client.get("/openapi.json").json()
    control = schema["components"]["schemas"]["MultiRolloutControl"]
    assert "WAITING_FEEDBACK" in control["properties"]["phase"]["enum"]
    assert "winner" in control["properties"]
    assert {"context", "request_id", "expected_revision"}.isdisjoint(
        schema["components"]["schemas"]["Interaction"]["properties"]
    )
    multi.bot.send_message.assert_not_awaited()


def test_default_di_exposes_explicit_fail_closed_capabilities():
    injector = Injector([ProductProfileRuntimeModule()], auto_bind=False)
    with pytest.raises(Invalid, match="not installed"):
        injector.get(MultiRolloutStrategyPort).require(policy(), (), None)
    with pytest.raises(Invalid, match="not installed"):
        injector.get(MultiRolloutIsolationPort).require("x", "y", (), None)
    with pytest.raises(Invalid, match="not installed"):
        injector.get(MultiRolloutNotificationPort).require()
