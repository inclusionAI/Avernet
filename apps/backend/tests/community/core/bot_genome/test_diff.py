"""``diff_specs``: added/removed/modified per gene, text diffs that apply back, the tier and rewrite flags."""
from __future__ import annotations

import json

import pytest

from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import canonical_tree_bytes
from agentclaw.community.core.bot_genome.canonical import canonicalize
from agentclaw.community.core.bot_genome.diff import diff_specs, unified_diff
from agentclaw.community.core.bot_genome.spec_models import (
    CenterSkill,
    GenomePolicy,
    GenomeSpec,
    MemorySpec,
    RepoSkill,
    ResourceEntry,
)
from agentclaw.community.core.bot_genome.text_edits import apply_unified_diff
from tests.community.core.bot_genome.example_blobs import MEMORY_BOT, SUPPORT_BOT, SUPPORT_BOT_CANDIDATE, BlobDir, policy_of, spec_of


def _diff(a: GenomeSpec, b: GenomeSpec, blobs: BlobDir | None = None, policy: GenomePolicy | None = None):
    return diff_specs(a, b, blobs or BlobDir(), revision="rev-b", against="rev-a", policy=policy)


def _soul(spec: GenomeSpec) -> str:
    return next(e.digest for e in spec.persona if e.type == "SOUL.md")


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("a\nb\n", "a\nc\n"),
        ("a\nb", "a\nc"),
        ("a\nb\n", "a\nb"),
        ("a\nb", "a\nb\n"),
        ("", "x\ny\n"),
        ("x\n", ""),
        ("a", "b\n"),
        ("x\n" * 20, "x\n" * 9 + "y\n" + "x\n" * 10),
        ("a\nb\nc", "a\nb\nc\nd"),
    ],
)
def test_unified_diff_applies_back_through_the_diff_kind(before: str, after: str) -> None:
    text = unified_diff(before, after)
    assert text == "" or text.startswith("@@")
    assert apply_unified_diff(before, text) == after


def test_the_example_candidate_against_its_parent() -> None:
    a, b = spec_of(SUPPORT_BOT), spec_of(SUPPORT_BOT_CANDIDATE)
    diff = _diff(a, b, policy=policy_of(SUPPORT_BOT_CANDIDATE))
    assert (diff.revision, diff.against, diff.risk_tier, diff.rewrite_flags) == ("rev-b", "rev-a", "T2", ())
    assert [(c.gene, c.kind) for c in diff.changes] == [("persona/SOUL.md", "modified"), ("resources/data/returns.md", "added")]
    soul_before = BlobDir().read(_soul(a)).decode()
    soul_after = BlobDir().read(_soul(b)).decode()
    assert apply_unified_diff(soul_before, diff.changes[0].diff) == soul_after
    assert diff.changes[1].diff is None
    reverse = _diff(b, a)
    assert [(c.gene, c.kind) for c in reverse.changes] == [("persona/SOUL.md", "modified"), ("resources/data/returns.md", "removed")]
    assert _diff(a, a).changes == () and _diff(a, a).risk_tier == "T0"


def test_skills_report_members_and_the_description_only_tier() -> None:
    blobs = BlobDir()
    a = spec_of(SUPPORT_BOT)
    skill_md = b"---\nname: quality-check\ndescription: Check a reply against the service rules before sending it.\n---\nRead RULES.md, then check the draft reply line by line.\n"
    check_py = b"import sys\n\nprint('ok' if sys.stdin.read().strip() else 'empty')\n"
    tweaked = skill_md.replace(b"Check a reply", b"Check every reply")
    b = spec_of(SUPPORT_BOT)
    b.skills[0].digest = blobs.put(canonical_tree_bytes([("SKILL.md", tweaked), ("scripts/check.py", check_py)]))
    diff = _diff(a, b, blobs)
    assert [(c.gene, c.kind) for c in diff.changes] == [("skills/quality-check", "modified"), ("skills/quality-check/SKILL.md", "modified")]
    assert diff.changes[1].diff.startswith("@@ -1,5 +1,5 @@\n ---\n name: quality-check\n-description: Check a reply")
    assert diff.risk_tier == "T1"
    c = spec_of(SUPPORT_BOT)
    c.skills[0].digest = blobs.put(canonical_tree_bytes([("SKILL.md", skill_md), ("docs/how to.md", b"# How\n")]))
    diff = _diff(a, c, blobs)
    assert [(x.gene, x.kind) for x in diff.changes] == [
        ("skills/quality-check", "modified"),
        ("skills/quality-check/docs/how%20to.md", "added"),
        ("skills/quality-check/scripts/check.py", "removed"),
    ]
    assert diff.risk_tier == "T2"
    d = spec_of(SUPPORT_BOT)
    d.skills[1] = CenterSkill(name="refund-policy", origin="center", center_version="v8")
    d.skills.append(RepoSkill(name="weather", origin="repo", repo_path="team/weather"))
    diff = _diff(a, d, blobs)
    assert [(x.gene, x.kind, x.diff) for x in diff.changes] == [("skills/refund-policy", "modified", None), ("skills/weather", "added", None)]


def test_resources_measure_text_files_and_flag_a_replacement_across_kinds() -> None:
    blobs = BlobDir()
    a = spec_of(SUPPORT_BOT)
    b = spec_of(SUPPORT_BOT)
    b.resources[0].digest = blobs.put(b"question,answer\nnew,answers\n")
    diff = _diff(a, b, blobs)
    assert [(c.gene, c.kind) for c in diff.changes] == [("resources/data/faq.csv", "modified")]
    assert diff.rewrite_flags == ("resources/data/faq.csv",) and diff.risk_tier == "T2"
    c = spec_of(SUPPORT_BOT)
    c.resources[0] = ResourceEntry(path="data/faq.csv/", digest=blobs.put(canonical_tree_bytes([("a.md", b"a\n")])))
    diff = _diff(a, c, blobs)
    assert [(x.gene, x.kind, x.diff) for x in diff.changes] == [("resources/data/faq.csv", "modified", None)]
    assert diff.rewrite_flags == ("resources/data/faq.csv",) and diff.risk_tier == "T2"
    d = spec_of(SUPPORT_BOT)
    d.resources[1].digest = blobs.put(canonical_tree_bytes([("returns/policy.md", b"# Returns\nReturns are accepted within 30 days of delivery.\n"), ("shipping.md", b"# Shipping\nStandard shipping takes 3-5 working days.\nFree over 50.\n")]))
    diff = _diff(a, d, blobs)
    assert [(x.gene, x.kind) for x in diff.changes] == [("resources/data/kb", "modified"), ("resources/data/kb/shipping.md", "modified")]
    assert diff.rewrite_flags == () and diff.risk_tier == "T1"
    # A member added or removed is what no file.edit can do: a patch replaces the directory, so the
    # diff measures it as that replacement (flagged, T2), not as an edit of the members that stayed.
    e = spec_of(SUPPORT_BOT)
    e.resources[1].digest = blobs.put(canonical_tree_bytes([("returns/policy.md", b"# Returns\nReturns are accepted within 30 days of delivery.\n"), ("shipping.md", b"# Shipping\nStandard shipping takes 3-5 working days.\n"), ("warranty.md", b"# Warranty\n")]))
    diff = _diff(a, e, blobs)
    assert [(x.gene, x.kind) for x in diff.changes] == [("resources/data/kb", "modified"), ("resources/data/kb/warranty.md", "added")]
    assert diff.rewrite_flags == ("resources/data/kb",) and diff.risk_tier == "T2"
    f = spec_of(SUPPORT_BOT)
    f.resources[1].digest = blobs.put(canonical_tree_bytes([("shipping.md", b"# Shipping\nStandard shipping takes 3-5 working days.\n")]))
    diff = _diff(a, f, blobs)
    assert [(x.gene, x.kind) for x in diff.changes] == [("resources/data/kb", "modified"), ("resources/data/kb/returns/policy.md", "removed")]
    assert diff.rewrite_flags == ("resources/data/kb",) and diff.risk_tier == "T2"


def test_memory_items_and_mode() -> None:
    blobs = BlobDir()
    a = spec_of(MEMORY_BOT)
    items = json.loads(blobs.read(a.memory.items_digest))
    b = spec_of(MEMORY_BOT)
    items[1]["text"] = "Returns are accepted within 45 days of delivery."
    del items[0]
    items.append({"key": "vip", "source": [], "tags": [], "text": "VIPs first."})
    b.memory = MemorySpec(mode="replace", items_digest=blobs.put(canonicalize(sorted(items, key=lambda i: i["key"]))))
    diff = _diff(a, b, blobs)
    assert [(c.gene, c.kind) for c in diff.changes] == [
        ("memory", "modified"),
        ("memory/customer-tier-rules", "removed"),
        ("memory/returns-window", "modified"),
        ("memory/vip", "added"),
    ]
    assert "-  \"text\": \"Returns are accepted within 30 days of delivery.\"" in diff.changes[2].diff
    assert diff.risk_tier == "T3"
    c = spec_of(MEMORY_BOT)
    c.memory = None
    assert [(x.gene, x.kind) for x in _diff(a, c, blobs).changes] == [("memory", "removed"), ("memory/customer-tier-rules", "removed"), ("memory/returns-window", "removed")]
    assert _diff(c, a, blobs).risk_tier == "T1"


def test_human_only_genes_are_t3_and_overrides_raise() -> None:
    blobs = BlobDir()
    a = spec_of(SUPPORT_BOT)
    b = spec_of(SUPPORT_BOT)
    b.tools.mcp[0].config.headers["X-Project"] = "sales"
    del b.tools.mcp[1]
    b.script = None
    diff = _diff(a, b, blobs)
    assert [(c.gene, c.kind) for c in diff.changes] == [("tools/mcp/mcp.x.meet", "modified"), ("tools/mcp/mcp.x.orders", "removed"), ("script", "removed")]
    assert '-      "X-Project": "support"' in diff.changes[0].diff
    assert diff.risk_tier == "T3"
    c = spec_of(SUPPORT_BOT)
    c.resources.append(ResourceEntry(path="data/new.md", digest=blobs.put(b"# New\n")))
    assert _diff(a, c, blobs).risk_tier == "T1"
    raised = GenomePolicy(mutable_genes=[], pins=[], risk_overrides={"resources/data": "T2"})
    assert _diff(a, c, blobs, policy=raised).risk_tier == "T2"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("x\na", "y\na\n"),
        ("x\na\n", "y\na"),
        ("a\nb\nc\nd\ne\nf\ng\nh\ni\nj", "X\nb\nc\nd\ne\nf\ng\nh\ni\nj\n"),
        ("a\x00<no newline>\nq\n", "a\x00<no newline>\nq"),  # a line may contain anything but a newline
    ],
)
def test_a_trailing_newline_flip_on_a_context_line_round_trips(before: str, after: str) -> None:
    assert apply_unified_diff(before, unified_diff(before, after)) == after


def test_a_skill_that_gains_a_file_beside_a_description_tweak_is_not_description_only() -> None:
    blobs = BlobDir()
    a = spec_of(SUPPORT_BOT)
    tweaked = b"---\nname: quality-check\ndescription: Check every reply against the service rules before sending it.\n---\nRead RULES.md, then check the draft reply line by line.\n"
    check_py = b"import sys\n\nprint('ok' if sys.stdin.read().strip() else 'empty')\n"
    b = spec_of(SUPPORT_BOT)
    b.skills[0].digest = blobs.put(canonical_tree_bytes([("SKILL.md", tweaked), ("scripts/check.py", check_py), ("evil.py", b"import os\n")]))
    assert _diff(a, b, blobs).risk_tier == "T2"
