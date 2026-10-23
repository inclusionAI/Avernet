"""The registry's canonical JSON against the contract's vectors (README §3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentclaw.community.core.bot_genome.canonical import (
    NotCanonicalizable,
    canonicalize,
    encode_segment,
    is_canonical_gene_path,
    loads_strict,
    revision_id,
    sha256_digest,
    tree_digest,
)

_CONTRACT = Path(__file__).resolve().parents[4] / "api-contracts" / "genome" / "v1"
_VECTORS = loads_strict((_CONTRACT / "canonical-json-vectors.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("vector", _VECTORS["canonical"], ids=lambda v: v["name"])
def test_canonical_vectors(vector: dict[str, Any]) -> None:
    canonical = canonicalize(loads_strict(vector["input"]))
    assert canonical.decode("utf-8") == vector["expected"]["canonical"]
    assert sha256_digest(canonical) == vector["expected"]["sha256"]


@pytest.mark.parametrize("vector", _VECTORS["rejected"], ids=lambda v: v["name"])
def test_rejected_vectors(vector: dict[str, Any]) -> None:
    with pytest.raises(NotCanonicalizable):
        canonicalize(loads_strict(vector["input"]))


@pytest.mark.parametrize("vector", _VECTORS["revision_ids"], ids=lambda v: v["name"])
def test_revision_id_vectors(vector: dict[str, Any]) -> None:
    spec, policy = vector["input"]["spec"], vector["input"]["policy"]
    assert revision_id(spec, policy) == vector["expected"]["revision_id"]


def test_bool_is_not_an_integer() -> None:
    assert canonicalize({"a": True, "b": 1}) == b'{"a":true,"b":1}'


def test_tree_digest_is_the_acm_tree_v1_digest() -> None:
    files = [("b.txt", b"2"), ("a.txt", b"1")]
    assert tree_digest(files) == tree_digest(list(reversed(files)))
    assert tree_digest(files).startswith("sha256:")


@pytest.mark.parametrize(
    ("name", "encoded"),
    [
        ("SOUL.md", "SOUL.md"),
        ("mcp:x", "mcp%3Ax"),
        (".", "%2E"),
        ("..", "%2E%2E"),
        ("docs/how to.md", "docs%2Fhow%20to.md"),
    ],
)
def test_encode_segment(name: str, encoded: str) -> None:
    assert encode_segment(name) == encoded


@pytest.mark.parametrize("path", ["persona", "persona/SOUL.md", "tools/mcp/mcp%3Ax", "resources/data/kb"])
def test_canonical_gene_paths(path: str) -> None:
    assert is_canonical_gene_path(path)


@pytest.mark.parametrize(
    "path", ["policy", "persona/SAFETY%2Emd", "skills/%61bc", "skills/%FF", "skills//x", "Persona"]
)
def test_non_canonical_gene_paths(path: str) -> None:
    assert not is_canonical_gene_path(path)
