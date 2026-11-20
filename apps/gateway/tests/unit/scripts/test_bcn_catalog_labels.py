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
        for key, value in node.items():
            child = (*path, key)
            if key == "catalog_labels" and path and path[-1] == "properties":
                found[child] = value
            else:
                found.update(_label_schemas(value, child))
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


def test_published_group_label_fields_match_authoritative_dump(label_contracts):
    published, generated = label_contracts
    expected = {
        path: schema
        for path, schema in _label_schemas(generated).items()
        if path[:3] == ("components", "schemas", "GroupDetail")
        or (
            path[0] == "paths"
            and path[1].startswith("/openapi/v1/collaboration/groups")
        )
    }
    assert expected, "the authoritative contract must expose catalog labels"
    actual = _label_schemas(published)
    for path, schema in expected.items():
        assert actual.get(path) == schema, path


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
