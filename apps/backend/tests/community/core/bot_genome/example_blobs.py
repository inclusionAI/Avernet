"""The contract's examples as test fixtures: the blobs directory as a ContentReader, the revisions by id."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentclaw.community.core.bot_genome.canonical import sha256_digest
from agentclaw.community.core.bot_genome.errors import ContentNotReachable

EXAMPLES = Path(__file__).resolve().parents[4] / "api-contracts" / "genome" / "v1" / "examples"
SUPPORT_BOT = "sha256:9e21ea360e6d4bdb63a151d280a7f1802402a8294b5033c92239731bd1e8d1fe"
SUPPORT_BOT_CANDIDATE = "sha256:5deca3495156b975acac9f4138966c19ce2d535943ce2ad625c238880373183b"
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
    out = {}
    for path in (EXAMPLES / "revisions").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        out[record["revision"]["id"]] = record
    return out


def load_invalid() -> dict[str, dict[str, Any]]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in (EXAMPLES / "invalid").glob("*.json")}


def load_patch(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / "patches" / name).read_text(encoding="utf-8"))
