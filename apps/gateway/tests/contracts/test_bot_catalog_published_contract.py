"""Published Gateway contract for Bot Catalog discovery."""

from __future__ import annotations

import json
from pathlib import Path

_ARTIFACT = (
    Path(__file__).resolve().parents[2] / "configs" / "schemas" / "bots.openapi.json"
)


def test_discover_publishes_query_contract() -> None:
    document = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    operation = document["paths"]["/openapi/v1/bots/catalog/discover"]["get"]
    parameters = {
        (parameter["in"], parameter["name"]): parameter
        for parameter in operation["parameters"]
    }
    min_score = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["name"] == "min_score" and parameter["in"] == "query"
    )

    assert min_score["schema"]["default"] == 0.01
    assert ("query", "viewer_actor_type") in parameters
    assert ("query", "viewer_actor_id") in parameters
