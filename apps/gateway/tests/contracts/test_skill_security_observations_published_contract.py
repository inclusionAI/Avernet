"""The Gateway catalog exposes the Backend Local Skill observation contract."""

from __future__ import annotations

import json
from pathlib import Path

_ARTIFACT = (
    Path(__file__).resolve().parents[2] / "configs" / "schemas" / "bots.openapi.json"
)
_PATH = "/openapi/v1/bots/{bot_id}/skills/{skill_id}/security-observations"


def test_skill_security_observations_route_and_response_are_published() -> None:
    document = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    operation = document["paths"][_PATH]["get"]
    assert (
        operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/Envelope_SkillSecurityObservations_"
    )
    schemas = document["components"]["schemas"]
    assert "SkillSecurityObservations" in schemas
    assert "SkillSecurityObservation" in schemas
    observation = schemas["SkillSecurityObservation"]
    assert set(observation["required"]) == {
        "observation_id",
        "sha256",
        "processing_status",
        "created_at",
    }
    assert "provider_revision" not in observation["properties"]
    assert "provider_revision" not in observation.get("example", {})
