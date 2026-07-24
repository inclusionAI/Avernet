"""Unit tests for the served-OpenAPI generator."""

from __future__ import annotations

from typing import Any

from gateway.community.core.authn import RouteSecurity
from gateway.community.core.forwarding import generate_openapi

_RULES = RouteSecurity.from_table(
    {
        "/**": ["first_party_user"],
        "GET /openapi/v1/bots/{id}": [{"first_party_user": {"scopes": ["bots:read"]}}],
    }
)


def _description() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "backend", "version": "1.0"},
        "paths": {
            "/openapi/v1/bots": {
                "post": {
                    "operationId": "create_bot",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/BotCreate"}
                            }
                        }
                    },
                    "responses": {"201": {"description": "created"}},
                }
            },
            "/openapi/v1/bots/{id}": {
                "get": {
                    "operationId": "get_bot",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Bot"}
                                }
                            }
                        }
                    },
                }
            },
            "/api/internal/debug": {"get": {"operationId": "debug"}},
        },
        "components": {
            "schemas": {
                "Bot": {
                    "type": "object",
                    "properties": {"owner": {"$ref": "#/components/schemas/Owner"}},
                },
                "Owner": {"type": "object"},
                "BotCreate": {"type": "object"},
                "Unused": {"type": "object"},
            }
        },
    }


def test_top_level_metadata_preserved() -> None:
    doc = generate_openapi(_description(), _RULES)
    assert doc["openapi"] == "3.1.0"
    assert doc["info"]["title"] == "backend"


def test_non_namespace_paths_filtered_out() -> None:
    doc = generate_openapi(_description(), _RULES)
    assert "/api/internal/debug" not in doc["paths"]
    assert "/openapi/v1/bots" in doc["paths"]
    assert "/openapi/v1/bots/{id}" in doc["paths"]


def test_security_attached_from_default_rule() -> None:
    doc = generate_openapi(_description(), _RULES)
    op = doc["paths"]["/openapi/v1/bots"]["post"]
    assert op["x-avernet-security"] == [{"first_party_user": {}}]


def test_security_attached_from_specific_rule() -> None:
    doc = generate_openapi(_description(), _RULES)
    op = doc["paths"]["/openapi/v1/bots/{id}"]["get"]
    assert op["x-avernet-security"] == [{"first_party_user": {"scopes": ["bots:read"]}}]


def test_referenced_components_kept_transitively() -> None:
    doc = generate_openapi(_description(), _RULES)
    schemas = doc["components"]["schemas"]
    assert set(schemas) == {
        "Bot",
        "Owner",
        "BotCreate",
    }  # Owner via Bot; Unused dropped


def test_discriminator_mapping_refs_are_kept() -> None:
    # A schema reachable only via discriminator.mapping (bare ref strings, not
    # a `$ref` key) must not be pruned, or the served doc dangles.
    description: dict[str, Any] = {
        "openapi": "3.1.0",
        "paths": {
            "/openapi/v1/bots/events": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Event"}
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "Event": {
                    "oneOf": [{"$ref": "#/components/schemas/Created"}],
                    "discriminator": {
                        "propertyName": "kind",
                        "mapping": {"created": "#/components/schemas/Created"},
                    },
                },
                "Created": {"type": "object"},
            }
        },
    }
    doc = generate_openapi(description, _RULES)
    assert set(doc["components"]["schemas"]) == {"Event", "Created"}


def test_input_description_not_mutated() -> None:
    description = _description()
    generate_openapi(description, _RULES)
    assert "x-avernet-security" not in description["paths"]["/openapi/v1/bots"]["post"]
    assert "Unused" in description["components"]["schemas"]


def test_empty_description_yields_empty_paths() -> None:
    doc = generate_openapi({"openapi": "3.1.0"}, _RULES)
    assert doc["paths"] == {}
    assert "components" not in doc
