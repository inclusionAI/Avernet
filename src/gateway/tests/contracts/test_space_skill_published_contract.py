"""Published Gateway contract for the Space Skill authoring/read loop."""

from __future__ import annotations

import json
from pathlib import Path

_ARTIFACT = (
    Path(__file__).resolve().parents[2] / "configs" / "schemas" / "bots.openapi.json"
)

_EXPECTED_OPERATIONS = {
    ("get", "/openapi/v1/bots/spaces/{space_id}/skills"),
    ("post", "/openapi/v1/bots/spaces/{space_id}/skills"),
    ("get", "/openapi/v1/bots/spaces/{space_id}/skills/consumable"),
    ("post", "/openapi/v1/bots/spaces/{space_id}/skills/import-from-git"),
    ("get", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}"),
    ("delete", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft"),
    ("get", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files"),
    ("get", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files/{path}"),
    ("put", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files/{path}"),
    (
        "post",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/refresh-from-git",
    ),
    ("post", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/upgrade"),
    ("get", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions"),
    ("get", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}"),
    (
        "get",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files",
    ),
    (
        "get",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files/{path}",
    ),
    ("get", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/grants"),
    (
        "put",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{manager_user_id}",
    ),
    (
        "delete",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{manager_user_id}",
    ),
    ("post", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/owner-transfer"),
    ("post", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/editor-requests"),
    ("get", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease"),
    ("put", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease"),
    ("delete", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease"),
    (
        "post",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease/takeover",
    ),
}


def test_published_artifact_contains_complete_space_skill_loop() -> None:
    document = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    paths = document["paths"]

    missing = {
        (method, path)
        for method, path in _EXPECTED_OPERATIONS
        if method not in paths.get(path, {})
    }

    assert not missing, f"generated bots OpenAPI is missing: {sorted(missing)}"
    schemas = document["components"]["schemas"]
    assert "SpaceSkillSummary" in schemas
    assert "SpaceSkillItem" not in schemas
