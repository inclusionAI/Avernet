"""Patch application against the contract's example patches and bases (README §6.1, §6.2, §6.4)."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import canonical_tree_bytes, decode_tree_bytes
from agentclaw.community.core.bot_genome.canonical import canonicalize, revision_id, sha256_digest
from agentclaw.community.core.bot_genome.errors import ContentNotReachable, PatchRejected
from agentclaw.community.core.bot_genome.patch import apply_patch, shape_reasons
from tests.community.core.bot_genome.example_blobs import (
    EXAMPLES,
    MEMORY_BOT,
    SUPPORT_BOT,
    SUPPORT_BOT_CANDIDATE,
    BlobDir,
    load_patch,
    load_revisions,
)

REVISIONS = load_revisions()
PATCHES = sorted((EXAMPLES / "patches").glob("*.json"))


def _rejected(spec: dict[str, Any], ops: list[dict[str, Any]], blobs: BlobDir | None = None) -> PatchRejected:
    with pytest.raises(PatchRejected) as caught:
        apply_patch(spec, {"ops": ops}, blobs or BlobDir())
    return caught.value


# -- the contract's examples ---------------------------------------------------------------------


@pytest.mark.parametrize("path", PATCHES, ids=lambda p: p.stem)
def test_every_example_patch_applies_to_its_base(path: Path) -> None:
    patch = json.loads(path.read_text(encoding="utf-8"))
    base = REVISIONS[patch["base"]]
    assert shape_reasons(patch) == []
    before = copy.deepcopy(base["spec"])
    outcome = apply_patch(base["spec"], patch, BlobDir())
    assert base["spec"] == before, "the base spec is never modified"
    assert len(outcome.touched) == len(patch["ops"])
    for digest, data in outcome.writes:
        assert sha256_digest(data) == digest
    for category, key in (("persona", "type"), ("skills", "name"), ("resources", "path")):
        keys = [entry[key] for entry in outcome.spec[category]]
        assert keys == sorted(keys) and len(keys) == len(set(keys))


def test_the_mixed_patch_reproduces_the_candidate_revision() -> None:
    patch = load_patch("00-mixed-r1-to-r2.json")
    base, candidate = REVISIONS[SUPPORT_BOT], REVISIONS[SUPPORT_BOT_CANDIDATE]
    outcome = apply_patch(base["spec"], patch, BlobDir())
    assert outcome.spec == candidate["spec"]
    assert revision_id(outcome.spec, base["policy"]) == candidate["revision"]["id"]
    assert sha256_digest(canonicalize(patch)) == candidate["revision"]["patch_digest"]
    written = dict(outcome.writes)
    assert set(written) == {next(e["digest"] for e in outcome.spec["persona"] if e["type"] == "SOUL.md")}


def test_skill_update_rewrites_the_tree_with_deletes_adds_and_diffs() -> None:
    patch = load_patch("06-skill-update.json")
    outcome = apply_patch(REVISIONS[SUPPORT_BOT]["spec"], patch, BlobDir())
    entry = next(e for e in outcome.spec["skills"] if e["name"] == "quality-check")
    members = dict(decode_tree_bytes(dict(outcome.writes)[entry["digest"]]))
    assert set(members) == {"SKILL.md", "scripts/check.sh", "assets/badge.png"}
    assert b"flag any promised amount" in members["SKILL.md"]
    assert outcome.touched[0].files == ("skills/quality-check/SKILL.md",)
    assert outcome.touched[0].description_only is False


def test_skill_update_description_only_is_detected_per_op() -> None:
    outcome = apply_patch(REVISIONS[SUPPORT_BOT]["spec"], load_patch("05-skill-update-description.json"), BlobDir())
    assert outcome.touched[0].description_only is True


def test_memory_ops_edit_the_stored_item_list() -> None:
    blobs = BlobDir()
    base = REVISIONS[MEMORY_BOT]["spec"]
    added = apply_patch(base, load_patch("10-memory-add.json"), blobs)
    items = json.loads(dict(added.writes)[added.spec["memory"]["items_digest"]])
    assert [item["key"] for item in items] == ["customer-tier-rules", "returns-window", "vip-contact"]
    retired = apply_patch(base, load_patch("12-memory-retire.json"), blobs)
    items = json.loads(dict(retired.writes)[retired.spec["memory"]["items_digest"]])
    assert [item["key"] for item in items] == ["returns-window"]
    mode = apply_patch(base, load_patch("13-memory-set-mode-replace.json"), blobs)
    assert mode.spec["memory"]["mode"] == "replace" and mode.touched[0].mode == "replace"


# -- refusals -------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["delete-skill-md", "unknown-op", "op-on-mcp", "diff-with-file-headers", "empty-rationale"])
def test_invalid_patch_examples_fail_the_shape_check(name: str) -> None:
    case = json.loads((EXAMPLES / "invalid" / f"{name}.json").read_text(encoding="utf-8"))
    assert case["check"] == "schema"
    reasons = shape_reasons(case["record"])
    assert reasons and all(reason.code == "schema" for reason in reasons)


@pytest.mark.parametrize(
    ("ops", "message"),
    [
        ([{"op": "file.edit", "target": "persona/OKR.md", "edits": [{"kind": "insert_after", "anchor": "x", "content": "y"}]}], "no persona file"),
        ([{"op": "file.edit", "target": "persona/SOUL.md", "edits": [{"kind": "insert_after", "anchor": "## Nope", "content": "y"}]}], "occurs 0 times"),
        ([{"op": "file.edit", "target": "persona/SOUL.md", "edits": [{"kind": "diff", "diff": "@@ -1 +1 @@\n-nope\n+x\n"}]}], "the diff expects"),
        ([{"op": "file.edit", "target": "skills/refund-policy/SKILL.md", "edits": [{"kind": "insert_after", "anchor": "x", "content": "y"}]}], "no local skill"),
        ([{"op": "file.edit", "target": "skills/quality-check/nope.md", "edits": [{"kind": "insert_after", "anchor": "x", "content": "y"}]}], "has no file"),
        ([{"op": "file.edit", "target": "resources/data/kb/nope.md", "edits": [{"kind": "insert_after", "anchor": "x", "content": "y"}]}], "has no file"),
        ([{"op": "file.edit", "target": "resources/data/nope.md", "edits": [{"kind": "insert_after", "anchor": "x", "content": "y"}]}], "no resource file"),
        ([{"op": "file.edit", "target": "script", "edits": [{"kind": "insert_after", "anchor": "x", "content": "y"}]}], "does not name"),
        ([{"op": "persona.add", "type": "SOUL.md", "content": "x"}], "already has persona file"),
        ([{"op": "persona.remove", "type": "OKR.md"}], "no persona file"),
        ([{"op": "skill.add", "name": "quality-check", "files": {"SKILL.md": "x"}}], "already has skill"),
        ([{"op": "skill.update", "name": "refund-policy", "files": {"a.md": "x"}}], "no local skill"),
        ([{"op": "skill.update", "name": "quality-check", "files": {"nope.md": None}}], "to delete"),
        ([{"op": "skill.update", "name": "quality-check", "file_diffs": [{"path": "nope.md", "diff": "@@ -1 +1 @@\n-a\n+b\n"}]}], "has no file"),
        ([{"op": "skill.update", "name": "quality-check", "file_diffs": [{"path": "SKILL.md", "diff": "@@ -1 +1 @@\n-a\n+b\n"}], "files": {"SKILL.md": "x"}}], "more than once"),
        ([{"op": "skill.remove", "name": "nope"}], "no skill"),
        ([{"op": "resource.add", "path": "data/faq.csv/", "content": {"digest": "sha256:30b884a411447c5dae5db89b6aab4c59114b454477be951e82d5e123622344ff"}}], "already has resource"),
        ([{"op": "resource.add", "path": "data/new/", "content": {"digest": "sha256:a5d2be13ca810cd6cb138660cf6809166c1f9e4ca2f422dc73ff2ed8c26358b5"}}], "not an acm-tree-v1 blob"),
        ([{"op": "resource.remove", "path": "data/kb"}], "no resource"),
    ],
)
def test_ops_that_do_not_apply_exactly(ops: list[dict[str, Any]], message: str) -> None:
    error = _rejected(REVISIONS[SUPPORT_BOT]["spec"], ops)
    assert [reason.code for reason in error.reasons] == ["does_not_apply"], error.reasons
    assert error.reasons[0].location == "ops[0]"
    assert message in error.reasons[0].message


def test_skill_update_refuses_to_delete_skill_md_even_past_the_schema() -> None:
    error = _rejected(REVISIONS[SUPPORT_BOT]["spec"], [{"op": "skill.update", "name": "quality-check", "files": {"SKILL.md": None}}])
    assert error.reasons[0].code == "does_not_apply" and "SKILL.md cannot be deleted" in error.reasons[0].message


@pytest.mark.parametrize(
    "op",
    [
        {"op": "memory.add", "item": {"key": "k", "text": "t", "tags": [], "source": []}},
        {"op": "memory.update", "item": {"key": "k", "text": "t", "tags": [], "source": []}},
        {"op": "memory.retire", "key": "k"},
        {"op": "memory.set_mode", "mode": "merge"},
    ],
)
def test_memory_ops_on_a_bot_without_memory(op: dict[str, Any]) -> None:
    error = _rejected(REVISIONS[SUPPORT_BOT]["spec"], [op])
    assert [reason.code for reason in error.reasons] == ["memory_disabled"]


def test_memory_add_of_a_present_key_and_update_of_an_absent_one() -> None:
    spec = REVISIONS[MEMORY_BOT]["spec"]
    present = {"key": "returns-window", "text": "t", "tags": [], "source": []}
    absent = {"key": "nope", "text": "t", "tags": [], "source": []}
    assert "already has an item" in _rejected(spec, [{"op": "memory.add", "item": present}]).reasons[0].message
    assert "has no item" in _rejected(spec, [{"op": "memory.update", "item": absent}]).reasons[0].message
    assert "has no item" in _rejected(spec, [{"op": "memory.retire", "key": "nope"}]).reasons[0].message


def test_a_binary_target_is_not_utf8() -> None:
    blobs = BlobDir()
    spec = copy.deepcopy(REVISIONS[SUPPORT_BOT]["spec"])
    spec["resources"].append({"path": "logo.png", "digest": blobs.put(b"\x89PNG\xff\xfe")})
    error = _rejected(spec, [{"op": "file.edit", "target": "resources/logo.png", "edits": [{"kind": "insert_after", "anchor": "x", "content": "y"}]}], blobs)
    assert error.reasons[0].code == "not_utf8"


def test_inline_content_over_the_byte_limit() -> None:
    big = "é" * 40000  # under the schema's 65536 code points, over 64 KiB of UTF-8
    error = _rejected(REVISIONS[SUPPORT_BOT]["spec"], [{"op": "persona.add", "type": "OKR.md", "content": big}])
    assert error.reasons[0].code == "limit_exceeded"
    error = _rejected(REVISIONS[SUPPORT_BOT]["spec"], [{"op": "file.edit", "target": "persona/SOUL.md", "edits": [{"kind": "insert_after", "anchor": "## Tone", "content": big}]}])
    assert error.reasons[0].code == "limit_exceeded"


def test_an_unknown_op_is_refused_as_schema() -> None:
    error = _rejected(REVISIONS[SUPPORT_BOT]["spec"], [{"op": "mcp.set"}])
    assert error.reasons[0].code == "schema"


def test_a_digest_the_bot_cannot_reach_propagates() -> None:
    with pytest.raises(ContentNotReachable):
        apply_patch(REVISIONS[SUPPORT_BOT]["spec"], {"ops": [{"op": "persona.add", "type": "OKR.md", "content": {"digest": "sha256:" + "0" * 64}}]}, BlobDir())


# -- replacement and change tracking ------------------------------------------------------------


def test_remove_then_add_is_a_replacement_measured_as_a_change() -> None:
    blobs = BlobDir()
    spec = REVISIONS[SUPPORT_BOT]["spec"]
    soul = blobs.read(next(e["digest"] for e in spec["persona"] if e["type"] == "SOUL.md"))
    outcome = apply_patch(
        spec,
        {"ops": [{"op": "persona.remove", "type": "SOUL.md"}, {"op": "persona.add", "type": "SOUL.md", "content": "# Soul\nRewritten.\n"}]},
        blobs,
    )
    assert outcome.replaced == {"persona/SOUL.md"}
    change = outcome.changed_files["persona/SOUL.md"]
    assert (change.before, change.after, change.text) == (soul, b"# Soul\nRewritten.\n", True)
    assert outcome.touched[1].files == ("persona/SOUL.md",)


def test_replacing_a_file_resource_by_a_directory_is_not_measured() -> None:
    tree = canonical_tree_bytes([("a.md", b"a\n")])
    blobs = BlobDir()
    digest = blobs.put(tree)
    outcome = apply_patch(
        REVISIONS[SUPPORT_BOT]["spec"],
        {"ops": [{"op": "resource.remove", "path": "data/faq.csv"}, {"op": "resource.add", "path": "data/faq.csv/", "content": {"digest": digest}}]},
        blobs,
    )
    assert outcome.replaced == {"resources/data/faq.csv"}
    assert outcome.changed_files["resources/data/faq.csv"].text is False


def test_a_file_edited_twice_is_tracked_once_from_base_to_result() -> None:
    spec = REVISIONS[SUPPORT_BOT]["spec"]
    blobs = BlobDir()
    before = blobs.read(next(e["digest"] for e in spec["persona"] if e["type"] == "SOUL.md"))
    ops = [
        {"op": "file.edit", "target": "persona/SOUL.md", "edits": [{"kind": "insert_after", "anchor": "## Tone", "content": "one"}]},
        {"op": "file.edit", "target": "persona/SOUL.md", "edits": [{"kind": "insert_after", "anchor": "one", "content": "two"}]},
    ]
    outcome = apply_patch(spec, {"ops": ops}, blobs)
    change = outcome.changed_files["persona/SOUL.md"]
    assert change.before == before and b"one\ntwo\n" in change.after
    assert len(outcome.changed_files) == 1


# -- review round 1: measuring rules ---------------------------------------------------------------


def test_skill_update_that_also_adds_or_deletes_a_file_is_not_description_only() -> None:
    spec = REVISIONS[SUPPORT_BOT]["spec"]
    tweak = "@@ -1,4 +1,4 @@\n ---\n name: quality-check\n-description: Check a reply against the service rules before sending it.\n+description: Check every draft.\n ---\n"
    for files in ({"evil.py": "import os\n"}, {"scripts/check.py": None}):
        outcome = apply_patch(spec, {"ops": [{"op": "skill.update", "name": "quality-check", "file_diffs": [{"path": "SKILL.md", "diff": tweak}], "files": files}]}, BlobDir())
        assert outcome.touched[0].description_only is False


def test_an_edit_then_remove_then_add_is_measured_from_the_base() -> None:
    blobs = BlobDir()
    spec = REVISIONS[SUPPORT_BOT]["spec"]
    base = blobs.read(next(e["digest"] for e in spec["persona"] if e["type"] == "SOUL.md"))
    ops = [
        {"op": "file.edit", "target": "persona/SOUL.md", "edits": [{"kind": "insert_after", "anchor": "## Tone", "content": "one"}]},
        {"op": "persona.remove", "type": "SOUL.md"},
        {"op": "persona.add", "type": "SOUL.md", "content": "# Soul\nRewritten.\n"},
    ]
    outcome = apply_patch(spec, {"ops": ops}, blobs)
    change = outcome.changed_files["persona/SOUL.md"]
    assert (change.before, change.after, change.text) == (base, b"# Soul\nRewritten.\n", True)


def test_a_replacement_across_kinds_stays_flagged_after_a_later_edit() -> None:
    blobs = BlobDir()
    ops = [
        {"op": "resource.remove", "path": "data/kb/"},
        {"op": "resource.add", "path": "data/kb", "content": "# KB\n"},
        {"op": "file.edit", "target": "resources/data/kb", "edits": [{"kind": "insert_after", "anchor": "# KB", "content": "x"}]},
    ]
    outcome = apply_patch(REVISIONS[SUPPORT_BOT]["spec"], {"ops": ops}, blobs)
    assert outcome.changed_files["resources/data/kb"].text is False


def test_files_the_patch_itself_adds_are_not_measured() -> None:
    blobs = BlobDir()
    ops = [
        {"op": "resource.add", "path": "data/y.md", "content": "a\n"},
        {"op": "file.edit", "target": "resources/data/y.md", "edits": [{"kind": "insert_after", "anchor": "a", "content": "b\nc\nd\n"}]},
        {"op": "skill.add", "name": "fresh", "files": {"SKILL.md": "---\nname: fresh\ndescription: d\n---\nx\n"}},
        {"op": "file.edit", "target": "skills/fresh/SKILL.md", "edits": [{"kind": "insert_after", "anchor": "x", "content": "y\nz\nw\n"}]},
        {"op": "skill.update", "name": "quality-check", "files": {"new.md": "n\n"}},
        {"op": "file.edit", "target": "skills/quality-check/new.md", "edits": [{"kind": "insert_after", "anchor": "n", "content": "m\no\np\n"}]},
        {"op": "resource.remove", "path": "data/y.md"},  # removing what the patch added is not a replacement either
        {"op": "resource.add", "path": "data/y.md", "content": "again\n"},
    ]
    outcome = apply_patch(REVISIONS[SUPPORT_BOT]["spec"], {"ops": ops}, blobs)
    assert outcome.changed_files == {} and outcome.replaced == frozenset()
