"""The contract's examples as test fixtures: the blobs directory as a ContentReader, the revisions by id, the patches as
models.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentclaw.community.core.bot_genome.canonical import sha256_digest
from agentclaw.community.core.bot_genome.errors import ContentNotReachable
from agentclaw.community.core.bot_genome.patch_models import GenomePatch
from agentclaw.community.core.bot_genome.spec_models import GenomePolicy, GenomeSpec

EXAMPLES = Path(__file__).resolve().parents[4] / "api-contracts" / "genome" / "v1" / "examples"
SUPPORT_BOT = "sha256:03dd8ae31e03dd12f9eca965000af1a4af950d867da49da4faa93f4d43153894"
SUPPORT_BOT_CANDIDATE = "sha256:74fef9e67c35b6f56f49307f1d84b670a43508a37952583f5d118baa3ba78e5f"
MEMORY_BOT = "sha256:22755f072e4c98e18595150e6ac125eb3300274ab668c37b731cc1bfd9cd3196"


class BlobDir:
    """``examples/blobs`` plus anything a test adds with ``put``."""

    def __init__(self) -> None:
        self.extra: dict[str, bytes] = {}

    def put(self, data: bytes) -> str:
        digest = sha256_digest(data)
        self.extra[digest] = data
        return digest

    def read(self, digest: str) -> bytes:
        if digest in self.extra:
            return self.extra[digest]
        path = EXAMPLES / "blobs" / digest.removeprefix("sha256:")
        if not path.exists():
            raise ContentNotReachable(digest)
        return path.read_bytes()


def load_revisions() -> dict[str, dict[str, Any]]:
    """Every example revision record (JSON) by its id."""
    out = {}
    for path in (EXAMPLES / "revisions").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        out[record["revision"]["id"]] = record
    return out


_REVISIONS = load_revisions()


def spec_of(record_id: str) -> GenomeSpec:
    """A fresh, independent ``GenomeSpec`` of an example revision; tests may mutate it."""
    return GenomeSpec.from_json(_REVISIONS[record_id]["spec"])


def policy_of(record_id: str) -> GenomePolicy:
    return GenomePolicy.from_json(_REVISIONS[record_id]["policy"])


def load_invalid() -> dict[str, dict[str, Any]]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in (EXAMPLES / "invalid").glob("*.json")}


def load_patch_json(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / "patches" / name).read_text(encoding="utf-8"))


def load_patch(name: str) -> GenomePatch:
    return GenomePatch.from_json(load_patch_json(name))
