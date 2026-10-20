"""``compile_spec`` against the contract's ``compiled.yaml`` cases; the document digest is of the canonical JSON."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agentclaw.community.core.bot_genome.canonical import canonicalize, sha256_digest
from agentclaw.community.core.bot_genome.compile import compile_spec, document_digest
from tests.community.core.bot_genome.example_blobs import EXAMPLES, BlobDir, load_revisions

CASES = sorted(p for p in (EXAMPLES / "manifest-v1").iterdir() if (p / "compiled.yaml").exists())


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_every_compiled_yaml_is_the_compiled_revision(case: Path) -> None:
    revision = json.loads((case / "revision.json").read_text(encoding="utf-8"))
    expected = yaml.safe_load((case / "compiled.yaml").read_text(encoding="utf-8"))
    compiled = compile_spec(revision["spec"], BlobDir(), revision_id=revision["revision"]["id"])
    assert compiled.document == expected
    assert compiled.revision_id == revision["revision"]["id"]
    assert compiled.document_digest == sha256_digest(canonicalize(expected))


def test_repo_and_center_skills_and_memory() -> None:
    spec = next(r for r in load_revisions().values() if any(s["origin"] == "repo" for s in r["spec"]["skills"]))["spec"]
    document = compile_spec(spec, BlobDir(), revision_id="r").document
    assert document["manifest"]["skills"] == [{"name": "weather", "source": {"protocol": "repo", "path": "team/weather"}}]
    assert "memory" not in document["manifest"] and "cli_tools" not in document["manifest"] and "engine_config" not in document["manifest"]
    assert document["script"] is None


def test_document_digest_does_not_depend_on_yaml_formatting() -> None:
    a = yaml.safe_load("schema_version: 1\nmanifest: {identity: [], skills: [], resources: [], mcp: []}\nscript: null\n")
    b = yaml.safe_load("script: ~\nmanifest:\n  mcp: []\n  resources: []\n  skills: []\n  identity: []\nschema_version: 1\n")
    assert document_digest(a) == document_digest(b)
