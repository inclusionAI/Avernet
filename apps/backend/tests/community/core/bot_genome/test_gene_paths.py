"""Gene path grammar: builders, covering, overlap (README §2, §6.1)."""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_genome.gene_paths import (
    covers,
    decode_segment,
    join,
    mcp_path,
    memory_path,
    op_gene_path,
    overlaps,
    persona_path,
    resource_path,
    skill_path,
    split,
)


def test_builders_use_the_contract_spellings() -> None:
    assert persona_path("SOUL.md") == "persona/SOUL.md"
    assert skill_path("refund-policy") == "skills/refund-policy"
    assert skill_path("refund-policy", "SKILL.md") == "skills/refund-policy/SKILL.md"
    assert skill_path("pdf", "docs/how to.md") == "skills/pdf/docs/how%20to.md"
    assert resource_path("data/kb/") == "resources/data/kb"
    assert resource_path("data/kb/", "faq.md") == "resources/data/kb/faq.md"
    assert resource_path("faq.csv") == "resources/faq.csv"
    assert memory_path() == "memory"
    assert memory_path("returns-window") == "memory/returns-window"
    assert mcp_path() == "tools/mcp"
    assert mcp_path("mcp:x") == "tools/mcp/mcp%3Ax"


def test_split_decodes_segments() -> None:
    assert split("tools/mcp/mcp%3Ax") == ("tools", ("mcp", "mcp:x"))
    assert split("skills/pdf/%2E%2E") == ("skills", ("pdf", ".."))
    with pytest.raises(ValueError):
        split("skills/%61bc")
    with pytest.raises(ValueError):
        decode_segment("%4")


def test_join_refuses_bad_input() -> None:
    with pytest.raises(ValueError):
        join("policy", "x")
    with pytest.raises(ValueError):
        join("skills", "a//b")


@pytest.mark.parametrize(
    ("rule", "path", "expected"),
    [
        ("skills/pdf", "skills/pdf/SKILL.md", True),
        ("skills/pdf", "skills/pdf", True),
        ("skills/pdf", "skills/pdf-tools", False),
        ("persona", "persona/SOUL.md", True),
        ("memory/x", "memory", False),
    ],
)
def test_covers(rule: str, path: str, expected: bool) -> None:
    assert covers(rule, path) is expected


def test_overlaps_is_symmetric() -> None:
    assert overlaps("skills/x/SKILL.md", "skills/x")
    assert overlaps("skills/x", "skills/x/SKILL.md")
    assert overlaps("memory/returns-window", "memory")
    assert not overlaps("skills/x", "skills/y")


@pytest.mark.parametrize(
    ("op", "path"),
    [
        ({"op": "file.edit", "target": "persona/SOUL.md", "edits": []}, "persona/SOUL.md"),
        ({"op": "persona.add", "type": "OKR.md", "content": ""}, "persona/OKR.md"),
        ({"op": "persona.remove", "type": "OKR.md"}, "persona/OKR.md"),
        ({"op": "skill.add", "name": "invoice-lookup", "files": {}}, "skills/invoice-lookup"),
        ({"op": "skill.update", "name": "refund-policy"}, "skills/refund-policy"),
        ({"op": "skill.remove", "name": "refund-policy"}, "skills/refund-policy"),
        ({"op": "resource.add", "path": "data/kb/", "content": {"digest": "x"}}, "resources/data/kb"),
        ({"op": "resource.remove", "path": "data/faq.csv"}, "resources/data/faq.csv"),
        ({"op": "memory.add", "item": {"key": "k"}}, "memory/k"),
        ({"op": "memory.update", "item": {"key": "k"}}, "memory/k"),
        ({"op": "memory.retire", "key": "k"}, "memory/k"),
        ({"op": "memory.set_mode", "mode": "seed"}, "memory"),
    ],
)
def test_op_gene_path(op: dict, path: str) -> None:
    assert op_gene_path(op) == path


def test_unknown_op_is_refused() -> None:
    with pytest.raises(ValueError):
        op_gene_path({"op": "tools.add"})
