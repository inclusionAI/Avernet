"""Published Gateway contract for patching one Bot's MCP overrides."""

import json
from pathlib import Path

import pytest
import yaml

from gateway.community.core.authn import RouteSecurity
from gateway.community.spi.authn import Presence, PrincipalType

GATEWAY = Path(__file__).resolve().parents[2]
PATH = "/openapi/v1/bots/{bot_id}/mcps/{server_code}/config"


@pytest.mark.parametrize("method", ["get", "put"])
def test_bot_mcp_config_is_published_and_admits_an_app(method):
    document = json.loads((GATEWAY / "configs/schemas/bots.openapi.json").read_text())
    operation = document["paths"][PATH][method]
    params = {param["name"] for param in operation["parameters"]}
    assert {"bot_id", "server_code", "entity_id", "user_id"} <= params
    config = yaml.safe_load((GATEWAY / "configs/application.yaml").read_text())
    security = RouteSecurity.from_table(config["user_config"]["route_security"])
    requirement = security.resolve(
        method.upper(), PATH.format(bot_id="bot", server_code="weather")
    )
    assert requirement[PrincipalType.APP] is Presence.OPTIONAL
    assert requirement[PrincipalType.USER] is Presence.OPTIONAL
    if method == "put":
        assert "423" in operation["responses"]
        body = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        schema = document["components"]["schemas"][body.rsplit("/", 1)[1]]
        assert set(schema["properties"]) == {"url", "headers"}
        assert schema["additionalProperties"] is False
        assert not schema.get("required")
