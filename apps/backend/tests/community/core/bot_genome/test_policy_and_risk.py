"""Policy checks (README §5, §6.4) and risk tiers (§6.3, ``risk-tiers.json``)."""
from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.bot_genome.contract import risk_tiers
from agentclaw.community.core.bot_genome.patch import evidence_reasons, patch_assessment, policy_reasons, shape_reasons
from agentclaw.community.core.bot_genome.patch.ops import FileChange, OpTouch, PatchOutcome
from agentclaw.community.core.bot_genome.patch.risk import apply_overrides, apply_raises, highest, op_tier, rewritten_files

POLICY = {"mutable_genes": ["memory", "persona", "resources", "skills"], "pins": ["persona/SAFETY.md"], "risk_overrides": {}}
EDIT = {"kind": "insert_after", "anchor": "x", "content": "y"}


def _patch(*ops: dict[str, Any], rationale: str = "why", evidence: list[str] | None = None) -> dict[str, Any]:
    return {"patch_schema": 1, "base": "sha256:" + "0" * 64, "ops": list(ops), "rationale": rationale, "evidence": [] if evidence is None else evidence}


# -- shape ------------------------------------------------------------------------------------------


def test_shape_reports_schema_errors_with_their_location() -> None:
    reasons = shape_reasons(_patch({"op": "persona.add", "type": "SOUL.md"}))
    assert reasons and reasons[0].code == "schema" and reasons[0].location == "ops[0]"
    reasons = shape_reasons(_patch({"op": "persona.remove", "type": "OKR.md"}, rationale="", evidence=["bad"]))
    assert [r.location for r in reasons] == ["evidence[0]", "rationale"]
    assert {r.location for r in shape_reasons({"patch_schema": 2})} == {"$", "patch_schema"}


def test_shape_reports_a_non_canonical_target() -> None:
    reasons = shape_reasons(_patch({"op": "file.edit", "target": "persona/%53OUL.md", "edits": [EDIT]}))
    assert [(r.code, r.location) for r in reasons] == [("not_canonical", "ops[0].target")]


# -- rationale and evidence -----------------------------------------------------------------------


def test_blank_rationale() -> None:
    reasons = evidence_reasons(_patch({"op": "persona.remove", "type": "OKR.md"}, rationale="  "))
    assert [r.code for r in reasons] == ["missing_rationale"]


@pytest.mark.parametrize(
    ("op", "needs"),
    [
        ({"op": "file.edit", "target": "persona/SOUL.md", "edits": [EDIT]}, True),
        ({"op": "file.edit", "target": "skills/x/SKILL.md", "edits": [EDIT]}, True),
        ({"op": "file.edit", "target": "resources/data/faq.csv", "edits": [EDIT]}, False),
        ({"op": "persona.add", "type": "OKR.md", "content": "x"}, True),
        ({"op": "persona.remove", "type": "OKR.md"}, False),
        ({"op": "skill.remove", "name": "x"}, True),
        ({"op": "resource.remove", "path": "a.md"}, True),
        ({"op": "memory.retire", "key": "k"}, False),
        ({"op": "memory.set_mode", "mode": "seed"}, True),
    ],
)
def test_evidence_is_required_per_op_kind(op: dict[str, Any], needs: bool) -> None:
    assert [r.code for r in evidence_reasons(_patch(op))] == (["missing_evidence"] if needs else [])
    assert evidence_reasons(_patch(op, evidence=["episode:ep_1"])) == []


# -- policy -----------------------------------------------------------------------------------------


def test_policy_allows_covered_unpinned_genes() -> None:
    ops = [{"op": "file.edit", "target": "persona/SOUL.md", "edits": [EDIT]}, {"op": "skill.remove", "name": "x"}, {"op": "memory.set_mode", "mode": "seed"}]
    assert policy_reasons(ops, POLICY) == []


def test_gene_not_mutable() -> None:
    policy = dict(POLICY, mutable_genes=["persona/SOUL.md", "skills/x"])
    ops = [
        {"op": "file.edit", "target": "persona/SOUL.md", "edits": [EDIT]},  # covered exactly
        {"op": "file.edit", "target": "skills/x/SKILL.md", "edits": [EDIT]},  # covered by a prefix
        {"op": "persona.add", "type": "OKR.md", "content": "x"},  # not covered
        {"op": "skill.update", "name": "xy", "files": {}},  # "skills/x" does not cover "skills/xy"
    ]
    assert [(r.code, r.location) for r in policy_reasons(ops, policy)] == [("gene_not_mutable", "ops[2]"), ("gene_not_mutable", "ops[3]")]


def test_pinned_item_blocks_the_item_its_parent_and_its_children() -> None:
    policy = dict(POLICY, pins=["persona/SAFETY.md", "skills/x/SKILL.md", "memory/k"])
    ops = [
        {"op": "persona.remove", "type": "SAFETY.md"},
        {"op": "skill.remove", "name": "x"},  # removing a skill with a pinned file
        {"op": "file.edit", "target": "skills/x/SKILL.md", "edits": [EDIT]},
        {"op": "memory.set_mode", "mode": "merge"},  # memory covers the pinned item
        {"op": "file.edit", "target": "skills/x/other.md", "edits": [EDIT]},  # sibling: fine
    ]
    assert [r.location for r in policy_reasons(ops, policy) if r.code == "pinned_item"] == ["ops[0]", "ops[1]", "ops[2]", "ops[3]"]


def test_gene_not_allowed_for_a_run() -> None:
    ops = [{"op": "persona.add", "type": "OKR.md", "content": "x"}, {"op": "skill.remove", "name": "x"}]
    reasons = policy_reasons(ops, POLICY, allowed_genes=["persona"])
    assert [(r.code, r.location) for r in reasons] == [("gene_not_allowed", "ops[1]")]
    assert policy_reasons(ops, POLICY, allowed_genes=["persona", "skills"]) == []


# -- risk -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("rule", risk_tiers()["rules"], ids=lambda r: r["reason"])
def test_every_rule_in_the_file_is_reached(rule: dict[str, Any]) -> None:
    when = rule.get("when", {})
    gene = rule["target"] if rule["target"] == "memory" else rule["target"] + "/x"
    touch = OpTouch(rule["op"], gene, description_only=when.get("description_only", False), mode=when.get("mode", "seed" if rule["op"] == "memory.set_mode" else None))
    assert op_tier(touch, risk_tiers()["rules"]) == rule["tier"]


def test_description_only_lowers_skill_edits_to_t1() -> None:
    rules = risk_tiers()["rules"]
    assert op_tier(OpTouch("file.edit", "skills/x/SKILL.md", description_only=True), rules) == "T1"
    assert op_tier(OpTouch("file.edit", "skills/x/SKILL.md"), rules) == "T2"
    assert op_tier(OpTouch("skill.update", "skills/x", description_only=True), rules) == "T1"
    assert op_tier(OpTouch("file.edit", "persona/SOUL.md", description_only=True), rules) == "T2"


def test_unmatched_op_is_an_error() -> None:
    with pytest.raises(ValueError):
        op_tier(OpTouch("file.edit", "tools/mcp/x"), risk_tiers()["rules"])


def test_highest_raises_and_overrides() -> None:
    assert highest(["T1", "T3", "T2"]) == "T3" and highest([]) == "T0"
    raises = risk_tiers()["raises"]
    assert apply_raises("T1", ["rewrite"], raises) == "T2"
    assert apply_raises("T3", ["rewrite"], raises) == "T3"
    assert apply_raises("T1", [], raises) == "T1"
    assert apply_overrides("T1", "skills/x", {"skills/x/SKILL.md": "T3"}) == "T3"  # overlap, not covering
    assert apply_overrides("T2", "skills/x", {"skills/x": "T1"}) == "T2"  # can only raise
    assert apply_overrides("T1", "skills/x", {"skills/y": "T3"}) == "T1"


def test_rewritten_files_measured_base_to_result_and_replacement_always() -> None:
    threshold = risk_tiers()["rewrite_threshold"]
    ten = "".join(f"l{i}\n" for i in range(10)).encode()
    four_changed = ten.replace(b"l1\n", b"x\n").replace(b"l2\n", b"y\n").replace(b"l3\n", b"z\n").replace(b"l4\n", b"w\n")
    five_changed = four_changed.replace(b"l5\n", b"v\n")
    changed = {
        "persona/A.md": FileChange(ten, four_changed),  # 4 of 10: changed = 8/20 = 2/5, not over
        "persona/B.md": FileChange(ten, five_changed),  # 5 of 10: 10/20 > 2/5
        "resources/d": FileChange(None, None, text=False),  # replacement across kinds
        "resources/bin": FileChange(b"\xff", b"\xfe"),  # not UTF-8 either side
        "skills/s/new.md": FileChange(None, b"new\n"),  # not in the base: not measured
    }
    assert rewritten_files(changed, threshold) == ("persona/B.md", "resources/bin", "resources/d")


def test_patch_assessment_takes_the_highest_tier_and_flags_the_ops_that_changed_a_rewritten_file() -> None:
    ten = "".join(f"l{i}\n" for i in range(10)).encode()
    rewritten = b"".join(b"n%d\n" % i for i in range(10))
    outcome = PatchOutcome(
        spec={},
        writes=(),
        touched=(
            OpTouch("file.edit", "resources/data/a.md", ("resources/data/a.md",)),
            OpTouch("file.edit", "resources/data/a.md", ("resources/data/a.md",)),
            OpTouch("memory.add", "memory/k"),
        ),
        changed_files={"resources/data/a.md": FileChange(ten, rewritten)},
    )
    tier, flags = patch_assessment(outcome, POLICY)
    assert (tier, flags) == ("T2", ("resources/data/a.md",))
    tier, flags = patch_assessment(PatchOutcome(spec={}, writes=(), touched=(OpTouch("memory.add", "memory/k"),)), POLICY)
    assert (tier, flags) == ("T1", ())
    override = dict(POLICY, risk_overrides={"memory": "T3"})
    assert patch_assessment(PatchOutcome(spec={}, writes=(), touched=(OpTouch("memory.add", "memory/k"),)), override)[0] == "T3"
