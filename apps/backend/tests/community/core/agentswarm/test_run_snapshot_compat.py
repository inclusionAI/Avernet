"""A run snapshot must stay readable by a release that predates definitions.

This is the property that makes rollback a plain previous-image deploy, so it is
asserted structurally rather than described in prose: a reference-submitted run's
stored catalog is decoded with a constructor built from the **pre-change** field
set only.
"""
from __future__ import annotations

import json

from agentclaw.community.core.agentswarm.domain import models as dm

# The AgentSpec fields as they stood before this change. A snapshot that carried
# any other key could not be read by an older release, whose AgentSpec(**c)
# rejects unknown keys.
PRE_CHANGE_FIELDS = (
    "name", "role", "instructions", "kind", "model", "engine", "bot_type",
    "identity_file", "metadata", "identity_bot_id", "config_manifest",
)


def _decode_as_old_release(catalog_json: str) -> list[dict]:
    """Decode the way an older release would: strict, no unknown keys."""
    rows = json.loads(catalog_json)
    out = []
    for row in rows:
        unknown = set(row) - set(PRE_CHANGE_FIELDS)
        assert not unknown, (
            f"snapshot carries keys an older release cannot decode: {unknown}"
        )
        out.append({k: row[k] for k in PRE_CHANGE_FIELDS if k in row})
    return out


def test_reference_submitted_catalog_decodes_as_an_older_release():
    definition = dm.AgentDefinition(
        definition_id=dm.AgentDefinitionId("7c1e"),
        tenant=dm.TenantId("teamclaw"),
        owner_id="49912",
        creator_id="49912",
        modifier_id="49912",
        name="hello_writer",
        role="负责产出 hello",
    )
    # Exactly how RunRepository.create serialises it.
    catalog_json = json.dumps(
        [a.__dict__ for a in (definition.to_spec(),)], ensure_ascii=False,
        default=str,
    )
    decoded = _decode_as_old_release(catalog_json)
    assert decoded[0]["name"] == "hello_writer"


def test_an_inline_catalog_decodes_identically():
    spec = dm.AgentSpec(name="hello_writer", role="负责产出 hello")
    catalog_json = json.dumps([spec.__dict__], ensure_ascii=False, default=str)
    assert _decode_as_old_release(catalog_json)[0]["name"] == "hello_writer"


def test_provenance_fields_are_not_on_the_spec():
    """Definition provenance belongs in its own table, never on the entry."""
    assert "definition_id" not in dm.AgentSpec.__dataclass_fields__
    assert "definition_row_id" not in dm.AgentSpec.__dataclass_fields__
