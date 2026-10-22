"""The typed contract models against the contract itself: every example round-trips, the shape rules bite, the value sets match."""
from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from agentclaw.community.core.bot_genome import contract
from agentclaw.community.core.bot_genome.canonical import canonicalize, revision_id, sha256_digest
from agentclaw.community.core.bot_genome.patch_models import GenomePatch, PatchOp, SkillUpdate, parse_ops
from agentclaw.community.core.bot_genome.spec_models import (
    GenomePolicy,
    GenomeSpec,
    McpConfig,
    McpServerEntry,
    MemoryMode,
    PersonaFileType,
    RiskTier,
    schema_property,
)
from tests.community.core.bot_genome.example_blobs import EXAMPLES, load_invalid

REVISION_FILES = sorted((EXAMPLES / "revisions").glob("*.json")) + sorted((EXAMPLES / "manifest-v1").glob("*/revision.json"))
PATCH_FILES = sorted((EXAMPLES / "patches").glob("*.json"))
INVALID = load_invalid()
_REV, _PATCH = contract.REVISION_SCHEMA_FILE, contract.PATCH_SCHEMA_FILE


def _literal_values(literal) -> set:
    return set(literal.__args__)


@pytest.mark.parametrize("path", REVISION_FILES, ids=lambda p: p.parent.name if p.name == "revision.json" else p.stem)
def test_every_example_revision_round_trips_and_keeps_its_id(path: Path) -> None:
    record = json.loads(path.read_text(encoding="utf-8"))
    spec, policy = GenomeSpec.from_json(record["spec"]), GenomePolicy.from_json(record["policy"])
    assert spec.to_json() == record["spec"] and policy.to_json() == record["policy"], "to_json is the contract's object, member for member"
    assert revision_id(spec, policy) == record["revision"]["id"] == revision_id(record["spec"], record["policy"])


@pytest.mark.parametrize("path", PATCH_FILES, ids=lambda p: p.stem)
def test_every_example_patch_round_trips_and_keeps_its_digest(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    patch = GenomePatch.from_json(raw)
    assert patch.to_json() == raw
    assert sha256_digest(canonicalize(patch.to_json())) == sha256_digest(canonicalize(raw))
    assert len(patch.ops) == len(raw["ops"]) and [op.op for op in patch.ops] == [op["op"] for op in raw["ops"]]


@pytest.mark.parametrize("name", sorted(n for n, c in INVALID.items() if c["check"] == "schema"))
def test_every_shape_invalid_example_is_refused_by_the_models(name: str) -> None:
    record = INVALID[name]["record"]
    with pytest.raises(ValidationError):
        if "patch_schema" in record:
            GenomePatch.from_json(record)
        else:
            GenomeSpec.from_json(record["spec"])
            GenomePolicy.from_json(record["policy"])


@pytest.mark.parametrize("name", sorted(n for n, c in INVALID.items() if c["check"] not in ("schema", "identity")))
def test_every_predicate_invalid_example_parses(name: str) -> None:
    """Order, trees, canonical spelling, UTF-8, admission: the registry's predicates, not the shape."""
    record = INVALID[name]["record"]
    if "patch_schema" in record:
        GenomePatch.from_json(record)
    else:
        GenomeSpec.from_json(record["spec"])
        GenomePolicy.from_json(record["policy"])


def test_absent_optional_members_stay_absent_and_nulls_stay_null() -> None:
    entry = McpServerEntry.from_json({"server_code": "a"})
    assert entry.config is None and entry.to_json() == {"server_code": "a"}
    for body in ({"server_code": "a", "config": None}, {"server_code": "a", "config": {"url": None}}):
        with pytest.raises(ValidationError):  # optional in the contract means left out, never null
            McpServerEntry.from_json(body)
    with pytest.raises(ValidationError):
        SkillUpdate.from_json({"op": "skill.update", "name": "x", "files": None, "file_diffs": [{"path": "a.md", "diff": "@@ -1 +1 @@\n-a\n+b\n"}]})
    assert McpServerEntry.from_json({"server_code": "a", "config": {}}).to_json() == {"server_code": "a", "config": {}}
    assert McpConfig.from_json({"url": "https://x.example.com/mcp"}).to_json() == {"url": "https://x.example.com/mcp"}
    update = SkillUpdate.from_json({"op": "skill.update", "name": "x", "files": {"a.md": None}})
    assert update.to_json() == {"op": "skill.update", "name": "x", "files": {"a.md": None}}, "a null file value is a delete, kept"
    spec = GenomeSpec.from_json({"persona": [], "skills": [], "memory": None, "resources": [], "tools": {"mcp": []}, "engine_config": {}, "script": None})
    assert spec.to_json()["memory"] is None and spec.to_json()["script"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"op": "skill.update", "name": "x"},  # neither file_diffs nor files
        {"op": "skill.update", "name": "x", "files": {"SKILL.md": None}},  # SKILL.md cannot be deleted
        {"op": "skill.add", "name": "x", "files": {"README.md": "x"}},  # no SKILL.md
        {"op": "resource.add", "path": "data/kb/", "content": "inline"},  # a directory comes from a tree
        {"op": "file.edit", "target": "script", "edits": [{"kind": "diff", "diff": "@@ -1 +1 @@\n-a\n+b\n"}]},  # not a file gene
        {"op": "file.edit", "target": "persona/SOUL.md", "edits": []},  # at least one edit
        {"op": "mcp.set", "server_code": "a"},  # no such op
        {"op": "persona.add", "type": "LESSONS.md", "content": "x"},  # not an identity file type
    ],
)
def test_the_cross_field_rules_of_the_contract_bite(body: dict) -> None:
    with pytest.raises(ValidationError):
        parse_ops([body])


def test_the_value_sets_are_the_contracts() -> None:
    assert _literal_values(PersonaFileType) == set(schema_property(_REV, "definitions", "personaType")["enum"])
    assert _literal_values(MemoryMode) == set(schema_property(_REV, "definitions", "memorySpec", "properties", "mode")["enum"])
    assert _literal_values(RiskTier) == set(schema_property(_REV, "definitions", "riskTier")["enum"])
    ops = {get_args(shape.model_fields["op"].annotation)[0] for shape in get_args(get_args(PatchOp)[0])}
    assert ops == set(schema_property(_PATCH, "definitions", "op", "properties", "op")["enum"])
    config = McpConfig.model_fields
    assert set(config) == set(schema_property(_REV, "definitions", "mcpConfig", "properties"))
