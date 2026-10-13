"""Decimal catalogue identifiers are validated before repository conversion."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.community.endpoints.test_openapi_skill_sets import (
    _BOT_ID,
    _HEADERS,
    _OWNER,
    _seed_inactive,
)


_SET_OPERATIONS = [
    ("GET", "", None),
    ("DELETE", "", None),
    ("PUT", "", {}),
    ("POST", "/activate", None),
    ("POST", "/deactivate", None),
    ("POST", "/mcp-permission-requests", {}),
    ("DELETE", "/skills/1", None),
    ("PUT", "/skills/1", None),
]


@pytest.mark.parametrize("method,suffix,payload", _SET_OPERATIONS)
@pytest.mark.parametrize(
    "set_id,expected",
    [
        ("test_id_001", 422),
        ("2147483648", 422),
        ("9" * 4301, 422),
        ("2147483647", 404),
        ("999999999", 404),
    ],
)
def test_skill_set_address_rejects_invalid_and_missing_ids(
    world, app_with_testing_modules, method, suffix, payload, set_id, expected
):
    _seed_inactive(world)
    client = TestClient(app_with_testing_modules, raise_server_exceptions=False)
    kwargs = {"headers": _HEADERS, "params": {"user_id": _OWNER}}
    if payload is not None:
        kwargs["json"] = payload
    response = client.request(
        method, f"/openapi/v1/bots/{_BOT_ID}/skill-sets/{set_id}{suffix}", **kwargs
    )
    assert response.status_code == expected, response.text
    assert response.json()["code"] == expected * 1000


@pytest.mark.parametrize(
    "skill_id,expected",
    [
        ("test_id_001", 422),
        ("-1", 422),
        ("1.5", 422),
        ("9223372036854775808", 422),
        ("9" * 4301, 422),
        ("9223372036854775807", 404),
        ("999999999", 404),
        ("00000000000000000001", 200),
        ("1", 200),
    ],
)
def test_repository_skill_address_retains_decimal_string_contract(
    world, app_with_testing_modules, skill_id, expected
):
    _seed_inactive(world)
    client = TestClient(app_with_testing_modules, raise_server_exceptions=False)
    response = client.get(
        f"/openapi/v1/bots/skills/repository/{skill_id}",
        headers=_HEADERS,
        params={"user_id": _OWNER},
    )
    assert response.status_code == expected, response.text
    assert response.json()["code"] == expected * 1000


def test_skill_set_address_retains_leading_zero_compatibility(
    world, app_with_testing_modules
):
    _seed_inactive(world)
    client = TestClient(app_with_testing_modules, raise_server_exceptions=False)
    response = client.get(
        f"/openapi/v1/bots/{_BOT_ID}/skill-sets/00000000001",
        headers=_HEADERS,
        params={"user_id": _OWNER},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["id"] == "1"


def test_skill_set_reference_address_has_the_same_decimal_validation():
    from tests.community.adapters.http.openapi_v1.conftest import public_document

    document = public_document()
    for path, operations in document["paths"].items():
        if "/skill-sets/{set_id}" not in path:
            continue
        for operation in operations.values():
            for parameter in operation.get("parameters", []):
                if parameter.get("name") == "set_id":
                    assert parameter["schema"]["type"] == "string"
                    assert parameter["schema"]["pattern"] == r"^[0-9]+$"
                    assert parameter["schema"]["maxLength"] == 4300


def test_repository_skill_address_documents_the_bounded_decimal_contract():
    from tests.community.adapters.http.openapi_v1.conftest import public_document

    operation = public_document()["paths"][
        "/openapi/v1/bots/skills/repository/{skill_id}"
    ]["get"]
    parameter = next(
        item for item in operation["parameters"] if item["name"] == "skill_id"
    )
    assert parameter["schema"]["type"] == "string"
    assert parameter["schema"]["pattern"] == r"^[0-9]+$"
    assert parameter["schema"]["maxLength"] == 4300
