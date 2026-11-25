"""Published label schema fragments must match the generated BCS contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_GATEWAY = Path(__file__).resolve().parents[3]


def _label_schemas(node: Any, path: tuple = ()) -> dict[tuple, Any]:
    found = {}
    if isinstance(node, dict):
        if "catalog_labels" in node.get("properties", {}):
            found[(*path, "properties", "catalog_labels")] = {
                "schema": node["properties"]["catalog_labels"],
                "required": "catalog_labels" in node.get("required", []),
            }
        for key, value in node.items():
            found.update(_label_schemas(value, (*path, key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.update(_label_schemas(value, (*path, index)))
    return found


@pytest.fixture(scope="module")
def label_contracts(tmp_path_factory):
    candidate = tmp_path_factory.mktemp("bcn-labels") / "generated.json"
    subprocess.run(
        [
            sys.executable,
            str(_GATEWAY.parent / "bcs/scripts/dump_openapi.py"),
            str(candidate),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    published = json.loads((_GATEWAY / "configs/schemas/bcn.openapi.json").read_text())
    generated = json.loads(candidate.read_text())
    return published, generated


def test_all_published_label_contracts_match_authoritative_dump(label_contracts):
    published, generated = label_contracts
    expected = _label_schemas(generated)
    assert expected, "the authoritative contract must expose catalog labels"
    # Include register, every expanded Group branch, and sibling required semantics.
    assert _label_schemas(published) == expected


def test_register_declares_the_complete_generated_optional_request_body(
    label_contracts,
):
    published, generated = label_contracts
    path = "/openapi/v1/collaboration/register"
    actual = published["paths"][path]["post"]["requestBody"]
    assert actual == generated["paths"][path]["post"]["requestBody"]
    assert not actual.get(
        "required", False
    )  # Existing query-only callers remain valid.
    fields = actual["content"]["application/json"]["schema"]["properties"]
    assert {
        "agent_card",
        "agent_card_url",
        "bot_name",
        "catalog_labels",
    } <= fields.keys()


def test_label_responses_are_required_but_request_labels_are_optional(label_contracts):
    published, _ = label_contracts
    contracts = _label_schemas(published)
    responses = {
        path: value
        for path, value in contracts.items()
        if "responses" in path or path[:3] == ("components", "schemas", "GroupDetail")
    }
    requests = {
        path: value for path, value in contracts.items() if "requestBody" in path
    }
    assert len(responses) == 10
    assert len(requests) == 4
    assert all(value["required"] for value in responses.values())
    assert all(not value["required"] for value in requests.values())


def test_group_patch_declares_optional_replace_and_clear_labels(label_contracts):
    published, generated = label_contracts
    path = "/openapi/v1/collaboration/groups/{group_id}"
    schema = published["paths"][path]["patch"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    upstream = generated["paths"][path]["patch"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert schema["additionalProperties"] is False
    assert "catalog_labels" not in schema.get("required", [])
    assert (
        schema["properties"]["catalog_labels"]
        == upstream["properties"]["catalog_labels"]
    )
    labels = schema["properties"]["catalog_labels"]
    assert labels["type"] == "object"  # {} is a valid explicit clear; null is not.
    assert labels.get("minProperties", 0) == 0
    assert labels["additionalProperties"]["oneOf"][0]["items"]["type"] == "string"
