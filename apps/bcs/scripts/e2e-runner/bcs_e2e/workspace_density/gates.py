"""Fail-closed validators for the group workspace density roster.

Extracted losslessly from contract.py by the workspace-density plane split;
frozen.py already names this module in its contract.  Every census,
partition, projection, roll-up, plumbing, cross-reference, address-census,
roster and story-step gate raises ``RunStateError`` on drift, so no stale
constant or hand-tuned row can silently become a PASS.  The content vs
liveness partition stays in contract.py because the assembly and the
public atom-row accessors consume it directly.
"""
from __future__ import annotations

from typing import Any

from ..run_state import RunStateError
from .catalog import address_key, load_migration_story
from .frozen import (
    ADDRESS_KEY_COUNT, ATOM_COUNT, ATOM_FAMILY_COUNTS, COMMITTED_COMMON_ATOMS,
    COMMITTED_FIELD_BASELINE, COMMITTED_FIELD_ROWS, COMMITTED_FIELD_TOTAL,
    COMMITTED_ONLY_ATOMS, DEPRECATED_CLASS, DEPRECATED_WORKSPACE_PARENT,
    DISTINCT_DISCLOSED_ATOMS, EXCLUSION_COUNT, EXCLUSION_FAMILY_COUNTS,
    EXCLUSION_ONLY_ADDRESS_KEY_COUNT, GROUP_SOURCE_FILE, LEAF_ADDRESS_KEY_COUNT,
    LEAF_COUNT, LEAF_FAMILY_COUNTS, LIVENESS_ONLY_TYPES, MINE_ONLY_ATOMS,
    PARSED_COUNT, PARENT_COUNT, PLUMBING_COUNTS, PLUMBING_TOTAL,
    STORIES_SOURCE_FILE, STORY_LINE_END, STORY_LINE_START,
)
def _gates_classification(content_rows: list[dict[str, Any]],
                          liveness_rows: list[dict[str, Any]]) -> None:
    if len(content_rows) != LEAF_COUNT or len(liveness_rows) != EXCLUSION_COUNT:
        raise RunStateError("workspace density 42/21 content-liveness partition changed")
    content_families = {name: sum(1 for row in content_rows if row["family"] == name)
                        for name in ATOM_FAMILY_COUNTS}
    if content_families != LEAF_FAMILY_COUNTS:
        raise RunStateError("workspace density leaf family census changed")
    exclusion_families = {name: sum(1 for row in liveness_rows if row["family"] == name)
                          for name in ATOM_FAMILY_COUNTS}
    if exclusion_families != EXCLUSION_FAMILY_COUNTS:
        raise RunStateError("workspace density exclusion family census changed")


def _gates_rollup_projection(rollup: dict[str, list[str]], rows: list[dict[str, Any]],
                             leaves: list[dict[str, Any]],
                             exclusions: list[dict[str, Any]]) -> None:
    flattened = [atom_id for atoms in rollup.values() for atom_id in atoms]
    atom_ids = [row["id"] for row in rows]
    if sorted(flattened) != sorted(atom_ids) or len(set(flattened)) != ATOM_COUNT:
        raise RunStateError("workspace density roll-up does not partition the 63 atoms")
    leaf_ids = {leaf["obligation_id"] for leaf in leaves}
    leaf_atoms = {leaf["atom_id"]: leaf for leaf in leaves}
    legacy_ids = {parent_id: None for parent_id in rollup}
    for parent_id in rollup:
        projection = set(rollup[parent_id])
        billed: set[str] = set()
        for leaf in leaves:
            if leaf["parent_obligation"] == parent_id:
                billed.add(leaf["atom_id"])
        for exclusion in exclusions:
            target = exclusion["charged_to"]
            if target == parent_id or (target in leaf_ids
                                       and leaf_atoms_id_parent(target, leaf_ids, leaves) == parent_id):
                billed.add(exclusion["atom_id"])
        if billed != projection:
            raise RunStateError(f"workspace density parent projection drifted: {parent_id}")
        if not projection:
            raise RunStateError(f"workspace density roll-up parent is empty: {parent_id}")
    for exclusion in exclusions:
        if exclusion["class"] == DEPRECATED_CLASS:
            if exclusion["charged_to"] != DEPRECATED_WORKSPACE_PARENT:
                raise RunStateError("workspace density deprecated 404 pair left its parent envelope")
            if exclusion["committed_field_obligation"] is None:
                raise RunStateError("workspace density deprecated pair lost its committed cross-reference")
            continue
        charged = exclusion["charged_to"]
        if charged not in leaf_ids:
            raise RunStateError(f"workspace density rider charged to a missing leaf: {charged}")
        charged_leaf = leaf_atoms[exclusion["charged_atom_id"]]
        if charged_leaf["obligation_id"] != charged:
            raise RunStateError("workspace density rider charge target drifted")
        if charged_leaf["parent_obligation"] not in rollup:
            raise RunStateError("workspace density rider left its roll-up parent")


def leaf_atoms_id_parent(target: str, leaf_ids: set[str], leaves: list[dict[str, Any]]) -> str:
    for leaf in leaves:
        if leaf["obligation_id"] == target:
            return leaf["parent_obligation"]
    raise RunStateError(f"workspace density charged leaf missing: {target}")


def _gates_plumbing(entries: list[dict[str, Any]], rows: list[dict[str, Any]],
                    parents: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["class"]] = counts.get(entry["class"], 0) + 1
    if counts != dict(PLUMBING_COUNTS):
        raise RunStateError(f"workspace density plumbing census drifted: {counts}")
    if len(entries) != PLUMBING_TOTAL:
        raise RunStateError("workspace density plumbing total changed")
    atom_lines: dict[str, set[int]] = {GROUP_SOURCE_FILE: set(), STORIES_SOURCE_FILE: set()}
    for row in rows:
        for number in range(row["line_start"], row["line_end"] + 1):
            atom_lines[row["source_file"]].add(number)
    parent_lines: set[int] = set()
    for parent in parents:
        for number in parent["story_calls"]:
            parent_lines.add(number)
        for item in parent["additional_story_calls"]:
            parent_lines.add(item["line"])
    for entry in entries:
        hit_atoms = [row["id"] for row in rows
                     if row["source_file"] == entry["source_file"]
                     and set(range(entry["line_start"], entry["line_end"] + 1))
                     & set(range(row["line_start"], row["line_end"] + 1))]
        entry["co_located_atom_ids"] = hit_atoms
        if entry["source_file"] == STORIES_SOURCE_FILE and entry["class"] == "story_tail_bind":
            entry["co_located_parent_ids"] = [
                parent["obligation_id"] for parent in parents
                if set(parent["story_calls"]) & set(range(entry["line_start"], entry["line_end"] + 1))]
        else:
            entry["co_located_parent_ids"] = []
        content_hits = [atom_id for atom_id in hit_atoms
                        if not _is_liveness_atom(rows, atom_id)]
        if content_hits:
            raise RunStateError(
                f"workspace density plumbing double-bills content atoms: {entry['class']} {content_hits}")


def _is_liveness_atom(rows: list[dict[str, Any]], atom_id: str) -> bool:
    row = next(item for item in rows if item["id"] == atom_id)
    kinds = {check.get("type") for check in row["checks"]}
    return kinds <= LIVENESS_ONLY_TYPES


def _gates_cross_reference(content_rows: list[dict[str, Any]],
                           liveness_rows: list[dict[str, Any]],
                           ledger: dict[str, Any]) -> dict[str, Any]:
    field_rows = [row for row in ledger["obligations"] if row.get("kind") == "FIELD"]
    baseline_rows = [row for row in ledger["obligations"] if row.get("kind") == "COARSE"]
    committed_atoms: set[str] = set()
    for row in field_rows:
        for atom_id in row.get("atom_ids") or []:
            committed_atoms.add(atom_id)
    if len(committed_atoms) != COMMITTED_FIELD_ROWS:
        raise RunStateError("workspace field ledger FIELD rows do not map 1:1 onto atoms")
    content_ids = {row["id"] for row in content_rows}
    liveness_ids = {row["id"] for row in liveness_rows}
    common = content_ids & committed_atoms
    committed_only = committed_atoms - content_ids - liveness_ids
    missing_from_committed = committed_atoms - content_ids - liveness_ids
    if committed_atoms - content_ids - liveness_ids != committed_atoms - content_ids:
        raise RunStateError("workspace field cross-reference arithmetic broke")
    deprecated_pair = {row["id"] for row in liveness_rows
                       if row["address"].get("operation") in ("workspace-put", "workspace-get")}
    if committed_only != deprecated_pair or len(committed_only) != COMMITTED_ONLY_ATOMS:
        raise RunStateError("workspace field committed-only atoms are not the deprecated 404 pair")
    mine_only = content_ids - committed_atoms
    if len(common) != COMMITTED_COMMON_ATOMS or len(mine_only) != MINE_ONLY_ATOMS:
        raise RunStateError("workspace field overlap census changed")
    if len(content_ids | committed_atoms) != DISTINCT_DISCLOSED_ATOMS:
        raise RunStateError("workspace field disclosed atom union changed")
    baseline_ids = [row["obligation_id"] for row in baseline_rows]
    if len(baseline_ids) != COMMITTED_FIELD_BASELINE or len(set(baseline_ids)) != COMMITTED_FIELD_BASELINE:
        raise RunStateError("workspace field ledger baseline rows changed")
    for row in field_rows:
        if row.get("parent_obligation") not in {item["id"] for item in _legacy_requirements()}:
            raise RunStateError("workspace field ledger parent drifted from the legacy roll-up")
    return {
        "committed_total": COMMITTED_FIELD_TOTAL,
        "committed_baseline": COMMITTED_FIELD_BASELINE,
        "committed_field_rows": COMMITTED_FIELD_ROWS,
        "committed_field_atom_union": len(committed_atoms),
        "common_atoms": len(common),
        "committed_only_atoms": len(committed_only),
        "committed_only_atom_ids": sorted(committed_only),
        "mine_only_atoms": len(mine_only),
        "distinct_disclosed_atoms": len(content_ids | committed_atoms),
        "note": (
            "24 atoms are common to the committed FIELD ledger and this roster; the 2 "
            "committed-only atoms are the deprecated workspace 404 pair folded here into "
            "the WORKSPACE.workspace parent envelope; the 18 mine-only atoms are the "
            "api residual content and cli residual content clauses below the FIELD slice"
        ),
    }


def _legacy_requirements() -> list[dict[str, Any]]:
    story = load_migration_story()
    return list(story.get("requirements") or [])


def _gates_address_census(rows: list[dict[str, Any]],
                          content_rows: list[dict[str, Any]]) -> dict[str, Any]:
    all_keys = {address_key(row["address"]) for row in rows}
    leaf_keys = {address_key(row["address"]) for row in content_rows}
    if len(all_keys) != ADDRESS_KEY_COUNT or len(leaf_keys) != LEAF_ADDRESS_KEY_COUNT:
        raise RunStateError("workspace density address census changed")
    exclusion_only = all_keys - leaf_keys
    if len(exclusion_only) != EXCLUSION_ONLY_ADDRESS_KEY_COUNT:
        raise RunStateError("workspace density exclusion-only address census changed")
    return {"all_keys": sorted(all_keys), "leaf_keys": sorted(leaf_keys),
            "exclusion_only_keys": sorted(exclusion_only),
            "total": len(all_keys), "leaf": len(leaf_keys),
            "exclusion_only": len(exclusion_only)}


def _roster(parents: list[dict[str, Any]], leaves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for leaf in leaves:
        by_parent.setdefault(leaf["parent_obligation"], []).append(leaf)
    roster: list[dict[str, Any]] = []
    for parent in parents:
        roster.append(parent)
        roster.extend(by_parent.get(parent["obligation_id"], []))
    ids = [row["obligation_id"] for row in roster]
    if len(ids) != len(set(ids)):
        raise RunStateError("workspace density roster ids are not unique")
    if len(roster) != PARSED_COUNT:
        raise RunStateError("workspace density parsed roster count changed")
    if sum(1 for row in roster if row["level"] == "parent") != PARENT_COUNT:
        raise RunStateError("workspace density parent roster changed")
    if sum(1 for row in roster if row["level"] == "field") != LEAF_COUNT:
        raise RunStateError("workspace density field roster changed")
    return roster


def _verify_story_steps(calls: list[tuple[int, str]], steps: list[dict[str, Any]]) -> None:
    if len(calls) != len(steps):
        raise RunStateError("workspace density story step census changed")
    derived = [name for _, name in calls]
    expected = [step["function"] for step in steps]
    if derived != expected:
        raise RunStateError("workspace density story body steps drifted from migration-contracts")
    if calls[0][0] != STORY_LINE_START + 3 or calls[-1][0] != STORY_LINE_END - 1:
        raise RunStateError("workspace density story call span moved")
