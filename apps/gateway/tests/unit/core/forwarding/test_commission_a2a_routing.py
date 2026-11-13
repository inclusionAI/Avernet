"""Commission A2A is a bounded gateway subtree with explicit backend rewrite."""

from pathlib import Path

import yaml

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


def test_commission_a2a_routes_to_backend_with_bounded_rewrite() -> None:
    raw = yaml.safe_load(_CONFIG.read_text())
    domains = DomainMap.from_config(raw["user_config"]["upstreams"], variables=_VARS)
    domain = domains.http_domain_for("/openapi/v1/task-commission/a2a")
    assert domain is not None
    assert domain.name == "task-commission-a2a"
    assert domain.server.name == "backend"
    assert domain.upstream_path("/openapi/v1/task-commission/a2a") == "/a2a/task-commission"
