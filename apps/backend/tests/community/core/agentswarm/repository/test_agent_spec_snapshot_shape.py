"""The run catalog snapshot must keep the key set it has today.

Rationale (design D4): the snapshot is stored as ``AgentSpec.__dict__`` and read
back through ``AgentSpec(**c)``, a dataclass that rejects unknown keys. Adding a
field to ``AgentSpec`` therefore breaks every release that predates it, which is
why definition provenance lives in its own table instead of on the catalog
entries. This test is the guard: if it fails, do not add the field — put the data
in ``ac_agent_swarm_run_agent_ref``.
"""
from __future__ import annotations

import json

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository.types import _dumps

# The exact serialised shape of one catalog entry. Pinned deliberately: this is
# a compatibility contract with already-deployed readers, not an incidental
# detail of the current dataclass.
EXPECTED_CATALOG_KEYS = [
    "name",
    "role",
    "instructions",
    "kind",
    "model",
    "engine",
    "bot_type",
    "identity_file",
    "metadata",
    "identity_bot_id",
    "config_manifest",
]


def _catalog_json(specs: tuple[dm.AgentSpec, ...]) -> str:
    """Serialise exactly as RunRepository.create does."""
    return _dumps([a.__dict__ for a in specs])


def test_catalog_entry_keys_are_unchanged():
    spec = dm.AgentSpec(name="hello_writer", role="负责产出 hello")
    encoded = _catalog_json((spec,))
    keys = list(json.loads(encoded)[0].keys())
    assert keys == EXPECTED_CATALOG_KEYS, (
        "AgentSpec's serialised key set changed. Run snapshots are read back "
        "through AgentSpec(**c), which rejects unknown keys, so adding a field "
        "breaks every older release. Put new provenance in "
        "ac_agent_swarm_run_agent_ref instead of on the spec."
    )


def test_a_definition_derived_spec_serialises_without_extra_keys():
    # A spec produced from a stored definition must be indistinguishable, on the
    # wire and in the snapshot, from the equivalent inline spec.
    definition = dm.AgentDefinition(
        definition_id=dm.AgentDefinitionId("7c1e"),
        tenant=dm.TenantId("teamclaw"),
        owner_id="49912",
        creator_id="49912",
        modifier_id="49912",
        name="hello_writer",
        role="负责产出 hello",
        instructions="只回复一个词：hello",
        kind=dm.AgentKind.BOT,
        engine="openclaw",
    )
    from_def = _catalog_json((definition.to_spec(),))
    inline = _catalog_json((
        dm.AgentSpec(
            name="hello_writer",
            role="负责产出 hello",
            instructions="只回复一个词：hello",
            kind=dm.AgentKind.BOT,
            engine="openclaw",
        ),
    ))
    assert from_def == inline


def test_old_stored_catalog_still_decodes():
    # A snapshot written before definitions existed: no definition_* keys.
    stored = json.dumps([{
        "name": "hello_writer",
        "role": "负责产出 hello",
        "instructions": "只回复一个词：hello",
        "kind": "bot",
        "model": None,
        "engine": "openclaw",
        "bot_type": "personal",
        "identity_file": "SOUL.md",
        "metadata": {},
        "identity_bot_id": "20261117_dfpm4nls",
        "config_manifest": "",
    }], ensure_ascii=False)

    decoded = [
        dm.AgentSpec(**{**c, "kind": dm.AgentKind(c.get("kind", "llm"))})
        for c in json.loads(stored)
    ]
    assert decoded[0].name == "hello_writer"
    assert decoded[0].identity_file == "SOUL.md"
