"""The frontend classification address resolves through the shipped Gateway."""

import json
from pathlib import Path

import yaml

from gateway.community.core.forwarding import DomainMap
from tests.unit.core.forwarding.test_domain_map import _VARS

_GATEWAY = Path(__file__).resolve().parents[4]


def test_classification_uses_the_backend_domain_and_published_minimal_schema():
    config = yaml.safe_load((_GATEWAY / "configs/application.yaml").read_text())
    domains = DomainMap.from_config(config["user_config"]["upstreams"], variables=_VARS)
    domain = domains.http_domain_for("/openapi/v1/bots/bot-1/classification")
    assert domain is not None
    assert domain.server.name == "backend"
    assert domain.schema.location == "schemas/bots.openapi.json"

    schema = json.loads((_GATEWAY / "configs/schemas/bots.openapi.json").read_text())
    operation = schema["paths"]["/openapi/v1/bots/{bot_id}/classification"]["get"]
    assert {parameter["name"] for parameter in operation["parameters"]} == {
        "bot_id",
        "entity_id",
    }
    assert {"200", "401", "404", "409", "422", "500"} <= operation["responses"].keys()
    assert set(schema["components"]["schemas"]["BotClassification"]["properties"]) == {
        "bot_id",
        "bot_type",
    }
