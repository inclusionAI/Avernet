"""Task-commission A2A remains backend-only, without a Gateway ingress."""

from pathlib import Path
from secrets import token_urlsafe
from unittest.mock import Mock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway.community.adapters.web._forward import forward_request
from gateway.community.core.forwarding import DomainMap

_CONFIG = Path(__file__).resolve().parents[4] / "configs" / "application.yaml"
_VARS = {
    "backend_server_url": "http://backend:8080",
    "baas_server_url": "http://baas:9090",
    "bcs_server_url": "http://bcs:8081",
    "engine_proxy_server_url": "https://engineproxy:20003",
    "bcsfuse_server_url": "http://bcsfuse:8765",
    "clawweb_server_url": "http://clawweb:8082",
}


@pytest.fixture
def domains() -> DomainMap:
    raw = yaml.safe_load(_CONFIG.read_text())
    return DomainMap.from_config(raw["user_config"]["upstreams"], variables=_VARS)


@pytest.mark.parametrize(
    "path",
    [
        "/openapi/v1/task-commission/a2a",
        "/openapi/v1/task-commission/a2a/",
        "/openapi/v1/task-commission/a2a/reports",
        "/a2a/task-commission",
    ],
)
def test_commission_a2a_is_not_exposed_by_gateway(
    domains: DomainMap, path: str
) -> None:
    assert domains.http_domain_for(path) is None
    assert domains.websocket_domain_for(path) is None


def test_commission_a2a_returns_404_without_authentication_or_forwarding(
    domains: DomainMap,
) -> None:
    app = FastAPI()
    app.state.domain_map = domains
    authenticator = app.state.authenticator = Mock(spec=["authenticate"])
    forwarder = app.state.forwarder = Mock(spec=["forward"])
    signer = app.state.principal_signer = Mock(spec=["sign"])
    app.add_api_route("/{path:path}", forward_request, methods=["POST"])

    with TestClient(app) as client:
        response = client.post(
            "/openapi/v1/task-commission/a2a",
            headers={"X-Avernet-Bot-Token": token_urlsafe(24)},
            json={},
        )

    assert response.status_code == 404
    assert response.json()["code"] == 404001
    assert response.json()["message"] == "no route for path"
    authenticator.authenticate.assert_not_called()
    forwarder.forward.assert_not_called()
    signer.sign.assert_not_called()


@pytest.mark.parametrize("base_path", ["/openapi/v1", "/api/v1"])
@pytest.mark.parametrize("resource", ["step", "stages", "interactions"])
def test_swarm_controls_still_route_to_backend(
    domains: DomainMap, base_path: str, resource: str
) -> None:
    path = (
        f"{base_path}/collaboration/task/context_collab/collet-test"
        f"/method-bindings/binding-test/swarm/{resource}"
    )
    domain = domains.http_domain_for(path)
    assert domain is not None
    assert domain.server.name == "backend"
    assert domain.upstream_path(path) == path
