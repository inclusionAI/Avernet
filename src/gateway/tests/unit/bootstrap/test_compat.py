"""Unit tests for the backward-compatibility checker."""

from __future__ import annotations

from typing import Any

from gateway.community.core.forwarding import check_compatible


def _spec(
    paths: dict[str, Any], schemas: dict[str, Any] | None = None
) -> dict[str, Any]:
    spec: dict[str, Any] = {"openapi": "3.1.0", "paths": paths}
    if schemas is not None:
        spec["components"] = {"schemas": schemas}
    return spec


def test_identical_is_compatible() -> None:
    spec = _spec({"/openapi/v1/bots": {"get": {"responses": {}}}})
    assert check_compatible(spec, spec) == []


def test_additive_operation_is_compatible() -> None:
    old = _spec({"/openapi/v1/bots": {"get": {}}})
    new = _spec(
        {"/openapi/v1/bots": {"get": {}, "post": {}}, "/openapi/v1/bots/x": {"get": {}}}
    )
    assert check_compatible(old, new) == []


def test_removed_operation_is_breaking() -> None:
    old = _spec({"/openapi/v1/bots": {"get": {}, "post": {}}})
    new = _spec({"/openapi/v1/bots": {"get": {}}})
    kinds = {b.kind for b in check_compatible(old, new)}
    assert "operation-removed" in kinds


def test_parameter_now_required_is_breaking() -> None:
    old = _spec({"/x": {"get": {"parameters": [{"name": "q", "in": "query"}]}}})
    new = _spec(
        {
            "/x": {
                "get": {"parameters": [{"name": "q", "in": "query", "required": True}]}
            }
        }
    )
    assert any(b.kind == "parameter-now-required" for b in check_compatible(old, new))


def test_new_required_parameter_is_breaking() -> None:
    old = _spec({"/x": {"get": {"parameters": []}}})
    new = _spec(
        {
            "/x": {
                "get": {"parameters": [{"name": "q", "in": "query", "required": True}]}
            }
        }
    )
    assert any(b.kind == "parameter-now-required" for b in check_compatible(old, new))


def test_added_optional_property_is_compatible() -> None:
    old = _spec({}, {"Bot": {"properties": {"id": {"type": "string"}}}})
    new = _spec(
        {},
        {"Bot": {"properties": {"id": {"type": "string"}, "name": {"type": "string"}}}},
    )
    assert check_compatible(old, new) == []


def test_removed_property_is_breaking() -> None:
    old = _spec(
        {},
        {"Bot": {"properties": {"id": {"type": "string"}, "name": {"type": "string"}}}},
    )
    new = _spec({}, {"Bot": {"properties": {"id": {"type": "string"}}}})
    assert any(b.kind == "property-removed" for b in check_compatible(old, new))


def test_property_now_required_is_breaking() -> None:
    old = _spec({}, {"Bot": {"properties": {"id": {"type": "string"}}}})
    new = _spec(
        {}, {"Bot": {"properties": {"id": {"type": "string"}}, "required": ["id"]}}
    )
    assert any(b.kind == "property-now-required" for b in check_compatible(old, new))


def test_type_change_is_breaking() -> None:
    old = _spec({}, {"Bot": {"properties": {"id": {"type": "string"}}}})
    new = _spec({}, {"Bot": {"properties": {"id": {"type": "integer"}}}})
    assert any(b.kind == "type-changed" for b in check_compatible(old, new))


def test_default_change_is_breaking() -> None:
    old = _spec({}, {"Bot": {"properties": {"n": {"type": "integer", "default": 1}}}})
    new = _spec({}, {"Bot": {"properties": {"n": {"type": "integer", "default": 2}}}})
    assert any(b.kind == "default-changed" for b in check_compatible(old, new))


def test_enum_value_removed_is_breaking() -> None:
    old = _spec({}, {"S": {"properties": {"k": {"enum": ["a", "b"]}}}})
    new = _spec({}, {"S": {"properties": {"k": {"enum": ["a"]}}}})
    assert any(b.kind == "enum-value-removed" for b in check_compatible(old, new))


def test_enum_value_added_is_compatible() -> None:
    old = _spec({}, {"S": {"properties": {"k": {"enum": ["a"]}}}})
    new = _spec({}, {"S": {"properties": {"k": {"enum": ["a", "b"]}}}})
    assert check_compatible(old, new) == []


def test_parameter_type_change_is_breaking() -> None:
    old = _spec(
        {
            "/x": {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "query", "schema": {"type": "string"}}
                    ]
                }
            }
        }
    )
    new = _spec(
        {
            "/x": {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "query", "schema": {"type": "integer"}}
                    ]
                }
            }
        }
    )
    assert any(b.kind == "type-changed" for b in check_compatible(old, new))


def test_parameter_enum_removal_is_breaking() -> None:
    old = _spec(
        {
            "/x": {
                "get": {
                    "parameters": [
                        {"name": "k", "in": "query", "schema": {"enum": ["a", "b"]}}
                    ]
                }
            }
        }
    )
    new = _spec(
        {
            "/x": {
                "get": {
                    "parameters": [
                        {"name": "k", "in": "query", "schema": {"enum": ["a"]}}
                    ]
                }
            }
        }
    )
    assert any(b.kind == "enum-value-removed" for b in check_compatible(old, new))


def test_request_body_now_required_is_breaking() -> None:
    old = _spec({"/x": {"post": {"requestBody": {"content": {}}}}})
    new = _spec({"/x": {"post": {"requestBody": {"required": True, "content": {}}}}})
    assert any(
        b.kind == "request-body-now-required" for b in check_compatible(old, new)
    )


def test_removed_schema_is_breaking() -> None:
    old = _spec({}, {"Bot": {"properties": {}}, "Owner": {"properties": {}}})
    new = _spec({}, {"Bot": {"properties": {}}})
    assert any(b.kind == "schema-removed" for b in check_compatible(old, new))
