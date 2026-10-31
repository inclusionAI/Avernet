"""Group workspace density roster: partition, builders and assembly.

Plane split (fixed_loop_density E2a pattern; completes the owning wave's
frozen.py/plumbing.py extraction):

- ``frozen.py`` — every frozen constant: SHA-256 freezes, line spans, family
  censuses, the live-true address census (36/27/9), plumbing census and the
  P0 determination plus the coverage-map snapshot lenses
- ``catalog.py`` — Obligation dataclass, SHA-gated loaders, shell span
  reader, fold/quote verifier, canonical atom address key
- ``gates.py`` — classification, roll-up projection, plumbing,
  cross-reference, address-census, roster and story-step fail-closed gates
- ``plumbing.py`` — mechanical 44-entry plumbing register scanner
- this module — content/liveness partition, rider exclusions, content
  leaves, legacy parents, roster assembly and plan projection

The split is lossless: the historical module surface is re-exported
unchanged below, and fail-closed behavior is byte-stable (the oracle quote
drift on WORKSPACE.cli.get.exit still raises exactly as before the split).
The address census adopts frozen.py's live-true 36/27/9 in place of the
stale inline 31/5; both decompositions sum to the frozen ADDRESS_KEY_COUNT
of 36, and the change is unreachable today because quote verification
raises first.  The two owning-wave semantic defects (single-line
exact-match against a substring oracle quote; the cross-reference
arithmetic guard contradicting the deprecated-pair committed-liveness
design) stay fail-closed and are disclosed in
~/.codex/bcs-e2e/migration-ledger-20261031.md.
"""
from __future__ import annotations

from typing import Any

from ..run_state import RunStateError
from .catalog import (  # noqa: F401  (historical surface re-exported unchanged)
    Obligation, _fold, _function_spans, _load_json_gated, _operation_display,
    _read_gated, _sha256, _span_contains, _verify_atom_quotes, _field_subjects,
    address_key, group_sh_lines, load_field_ledger, load_migration_story,
    load_oracle, stories_sh_lines,
)
from .frozen import (  # noqa: F401  (historical surface re-exported unchanged)
    SCHEMA_VERSION, PARENT, CASE_ID, CONTRACT, ORACLE_CONTRACT, FIELD_ORACLE_CONTRACT,
    MIGRATION_CONTRACT, RUNNER_ROOT, E2E_TEST_ROOT, GROUP_SOURCE_FILE, STORIES_SOURCE_FILE, GROUP_SH_PATH,
    STORIES_SH_PATH, ORACLE_PATH, FIELD_ORACLE_PATH, MIGRATION_PATH, GROUP_SH_SHA256, STORIES_SH_SHA256,
    ORACLE_SHA256, FIELD_ORACLE_SHA256, MIGRATION_SHA256, STORY_LINE_START, STORY_LINE_END, TAIL_LINE_START,
    TAIL_LINE_END, GROUP_SCOPED_FIRST, GROUP_SCOPED_LAST, ATOM_COUNT, ATOM_FAMILY_COUNTS, LEAF_COUNT,
    EXCLUSION_COUNT, PARENT_COUNT, PARSED_COUNT, RESIDUAL_COUNT_GAP, LEAF_FAMILY_COUNTS, EXCLUSION_FAMILY_COUNTS,
    COMMITTED_FIELD_TOTAL, COMMITTED_FIELD_BASELINE, COMMITTED_FIELD_ROWS, COMMITTED_COMMON_ATOMS, COMMITTED_ONLY_ATOMS, MINE_ONLY_ATOMS,
    COMMITTED_FIELD_UNION, DISTINCT_DISCLOSED_ATOMS, ADDRESS_KEY_COUNT, LEAF_ADDRESS_KEY_COUNT, EXCLUSION_ONLY_ADDRESS_KEY_COUNT, PLUMBING_TOTAL,
    PLUMBING_COUNTS, LIVENESS_ONLY_TYPES, DEPRECATED_WORKSPACE_PARENT, DEPRECATED_CLASS, RIDER_CLASS, METHOD_VERB,
    STORY_CALL, FUNCTION_OPEN, CONSTANT_ENV_FAKE_GROUP, P0_DETERMINATION, _MAP_SNAPSHOT_ROW_7, _MAP_RANK1_BASIS,
)
from .gates import (  # noqa: F401  (historical surface re-exported unchanged)
    _gates_address_census, _gates_classification, _gates_cross_reference,
    _gates_plumbing, _gates_rollup_projection, _is_liveness_atom, _legacy_requirements,
    _roster, _verify_story_steps, leaf_atoms_id_parent,
)
from .plumbing import scan_plumbing as _scan_plumbing  # historical private name
def _classify(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    content: list[dict[str, Any]] = []
    liveness: list[dict[str, Any]] = []
    for row in rows:
        kinds = {check.get("type") for check in row["checks"]}
        if not kinds:
            raise RunStateError(f"workspace density atom lost its checks: {row['id']}")
        if kinds <= LIVENESS_ONLY_TYPES:
            liveness.append(row)
        else:
            content.append(row)
    return content, liveness


def _committed_field_map(ledger: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in ledger["obligations"]:
        if row.get("kind") != "FIELD":
            continue
        for atom_id in row.get("atom_ids") or []:
            if atom_id in mapping:
                raise RunStateError("workspace field ledger atom mapped twice")
            mapping[atom_id] = row["obligation_id"]
    return mapping


def _build_exclusions(liveness_rows: list[dict[str, Any]], all_rows: list[dict[str, Any]],
                      rollup: dict[str, list[str]],
                      committed_field: dict[str, str]) -> list[dict[str, Any]]:
    full_index = {row["id"]: number for number, row in enumerate(all_rows)}
    content_ids = {row["id"] for row in all_rows
                   if {check.get("type") for check in row["checks"]} - LIVENESS_ONLY_TYPES}
    parent_atoms: dict[str, list[str]] = {}
    for atom_id in full_index:
        owners = [legacy_id for legacy_id, atoms in rollup.items() if atom_id in atoms]
        if len(owners) != 1:
            raise RunStateError(f"workspace density atom roll-up ownership broke: {atom_id}")
        parent_atoms.setdefault(owners[0], []).append(atom_id)
    exclusions: list[dict[str, Any]] = []
    for row in liveness_rows:
        atom_id = row["id"]
        owners = [legacy_id for legacy_id, atoms in rollup.items() if atom_id in atoms]
        if len(owners) != 1:
            raise RunStateError(f"workspace density liveness atom roll-up broke: {atom_id}")
        legacy_id = owners[0]
        siblings = sorted(parent_atoms[legacy_id], key=lambda item: full_index[item])
        content_siblings = [item for item in siblings if item in content_ids and item != atom_id]
        following = [item for item in content_siblings if full_index[item] > full_index[atom_id]]
        if following:
            charged_atom = following[0]
            exclusion_class = RIDER_CLASS
            charged_to = "WORKSPACE_DENSITY." + charged_atom.split(".", 1)[1]
            charged_level = "field"
            reason = (
                "success-liveness rider: its status/exit precondition is carried by the "
                "charged content leaf of the same roll-up parent; billing it again would "
                "double-count one upstream clause"
            )
        elif content_siblings:
            charged_atom = content_siblings[0]
            exclusion_class = RIDER_CLASS + "_PRECEDING"
            charged_to = "WORKSPACE_DENSITY." + charged_atom.split(".", 1)[1]
            charged_level = "field"
            reason = (
                "success-liveness rider preceding the parent's first content leaf; "
                "charged once to that leaf"
            )
        else:
            if legacy_id != DEPRECATED_WORKSPACE_PARENT:
                raise RunStateError(f"workspace density parent lost all content atoms: {legacy_id}")
            charged_atom = None
            exclusion_class = DEPRECATED_CLASS
            charged_to = DEPRECATED_WORKSPACE_PARENT
            charged_level = "parent"
            reason = (
                "deprecated /groups/{id}/workspace 404 envelope: the legacy WORKSPACE.workspace "
                "expectation carries the 404 contract verbatim, so both the write-gone and "
                "read-gone clauses are charged once at the parent envelope instead of becoming "
                "fake PASS leaves; the committed FIELD ledger registers the same two atoms and "
                "this package folds them into the parent envelope without double billing"
            )
        if charged_atom is not None:
            charged_family = next(item["family"] for item in all_rows if item["id"] == charged_atom)
            if charged_family != row["family"]:
                raise RunStateError(f"workspace density rider crosses families: {atom_id}")
        match = METHOD_VERB.match(row["source_quote"])
        exclusions.append({
            "exclusion_id": atom_id,
            "atom_id": atom_id,
            "class": exclusion_class,
            "family": row["family"],
            "level": "exclusion",
            "method": match.group(1) if match else "assert",
            "line_start": row["line_start"],
            "line_end": row["line_end"],
            "quote": row["source_quote"],
            "expectation": row["expectation"],
            "operation": _operation_display(row["address"]),
            "field_subject": _field_subjects(row["checks"]),
            "basis": "+".join(str(check.get("type")) for check in row["checks"]),
            "checks": list(row["checks"]),
            "address": dict(row["address"]),
            "address_key": address_key(row["address"]),
            "source_file": row["source_file"],
            "source_function": row["source_function"],
            "charged_to": charged_to,
            "charged_to_level": charged_level,
            "charged_atom_id": charged_atom,
            "committed_field_obligation": committed_field.get(atom_id),
            "reason": reason,
        })
    if len(exclusions) != EXCLUSION_COUNT:
        raise RunStateError("workspace density exclusion census changed")
    return exclusions


def _build_leaves(content_rows: list[dict[str, Any]], rollup: dict[str, list[str]],
                  committed_field: dict[str, str]) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    for row in content_rows:
        owners = [legacy_id for legacy_id, atoms in rollup.items() if row["id"] in atoms]
        if len(owners) != 1:
            raise RunStateError(f"workspace density content atom roll-up broke: {row['id']}")
        match = METHOD_VERB.match(row["source_quote"])
        leaves.append({
            "obligation_id": "WORKSPACE_DENSITY." + row["id"].split(".", 1)[1],
            "parent_obligation": owners[0],
            "level": "field",
            "family": row["family"],
            "method": match.group(1) if match else "assert",
            "line_start": row["line_start"],
            "line_end": row["line_end"],
            "quote": row["source_quote"],
            "expectation": row["expectation"],
            "operation": _operation_display(row["address"]),
            "field_subject": _field_subjects(row["checks"]),
            "basis": "+".join(str(check.get("type")) for check in row["checks"]),
            "expected": list(row["checks"]),
            "density": True,
            "source_file": row["source_file"],
            "source_function": row["source_function"],
            "address": dict(row["address"]),
            "address_key": address_key(row["address"]),
            "atom_id": row["id"],
            "committed_field_obligation": committed_field.get(row["id"]),
        })
    if len(leaves) != LEAF_COUNT:
        raise RunStateError("workspace density leaf census changed")
    return leaves


def _story_step_calls(lines: list[str]) -> list[tuple[int, str]]:
    calls: list[tuple[int, str]] = []
    for number in range(STORY_LINE_START, STORY_LINE_END + 1):
        match = STORY_CALL.match(lines[number - 1])
        if match:
            calls.append((number, match.group(1)))
    return calls


def _build_parents(requirements: list[dict[str, Any]], calls: list[tuple[int, str]],
                   stories_lines: list[str], steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    call_lines = {name: number for number, name in calls}
    if len(call_lines) != len(calls):
        raise RunStateError("workspace density story body calls are not unique")
    step_sources = {step["function"]: step["source"] for step in steps}
    parents: list[dict[str, Any]] = []
    for requirement in requirements:
        functions = list(requirement.get("functions") or [])
        if not functions:
            raise RunStateError(f"workspace density legacy requirement lost functions: {requirement['id']}")
        own = sorted(call_lines[name] for name in functions)
        if len(own) != len(functions) or any(name not in call_lines for name in functions):
            raise RunStateError(f"workspace density parent step not found in story body: {requirement['id']}")
        sources = {step_sources[name] for name in functions}
        if len(sources) != 1:
            raise RunStateError(f"workspace density parent spans two source files: {requirement['id']}")
        contiguous = own == list(range(own[0], own[-1] + 1))
        if contiguous:
            line_start, line_end = own[0], own[-1]
            quote = "\n".join(stories_lines[line_start - 1:line_end])
            additional: tuple[dict[str, Any], ...] = ()
            interleaved: tuple[int, ...] = ()
        else:
            line_start = line_end = own[0]
            quote = stories_lines[own[0] - 1]
            additional = tuple(
                {"line": number, "quote": stories_lines[number - 1]} for number in own[1:])
            interleaved = tuple(
                number for number in range(own[0], own[-1] + 1) if number not in set(own))
        parents.append({
            "obligation_id": requirement["id"],
            "parent_obligation": PARENT,
            "level": "parent",
            "family": "legacy-rollup",
            "method": "story_step_call",
            "line_start": line_start,
            "line_end": line_end,
            "quote": quote,
            "expectation": requirement["expectation"],
            "operation": "story_step:" + "+".join(functions),
            "field_subject": "story_calls",
            "basis": "legacy_requirement_rollup",
            "expected": None,
            "density": False,
            "source_file": next(iter(sources)),
            "source_function": "+".join(functions),
            "address": None,
            "address_key": None,
            "atom_id": None,
            "story_calls": tuple(own),
            "additional_story_calls": additional,
            "interleaved_story_calls": interleaved,
            "committed_field_obligation": requirement["id"],
            "legacy_functions": tuple(functions),
        })
    return parents


def _assemble() -> tuple[dict[str, Any], tuple[Obligation, ...]]:
    group_raw, group_lines = _read_gated(GROUP_SH_PATH, GROUP_SH_SHA256, "group.sh")
    stories_raw, stories_lines = _read_gated(STORIES_SH_PATH, STORIES_SH_SHA256, "stories.sh")
    oracle = load_oracle()
    ledger = load_field_ledger()
    story = load_migration_story()
    rows = list(oracle["density_requirements"])
    group_spans = _function_spans(group_lines)
    stories_spans = _function_spans(stories_lines)
    _verify_atom_quotes(rows, {GROUP_SOURCE_FILE: group_lines, STORIES_SOURCE_FILE: stories_lines},
                        {GROUP_SOURCE_FILE: group_spans, STORIES_SOURCE_FILE: stories_spans})
    requirements = list(story.get("requirements") or [])
    steps = list(story.get("steps") or [])
    corrections = list(story.get("corrections") or [])
    capability_gaps = list(story.get("capability_gaps") or [])
    content_rows, liveness_rows = _classify(rows)
    _gates_classification(content_rows, liveness_rows)
    rollup = dict(ledger.get("baseline_atom_rollup") or {})
    if len(rollup) != PARENT_COUNT:
        raise RunStateError("workspace density roll-up parent census changed")
    committed_field = _committed_field_map(ledger)
    cross_reference = _gates_cross_reference(content_rows, liveness_rows, ledger)
    calls = _story_step_calls(stories_lines)
    _verify_story_steps(calls, steps)
    parents = _build_parents(requirements, calls, stories_lines, steps)
    leaves = _build_leaves(content_rows, rollup, committed_field)
    exclusions = _build_exclusions(liveness_rows, rows, rollup, committed_field)
    _gates_rollup_projection(rollup, rows, leaves, exclusions)
    plumbing = _scan_plumbing(group_lines, stories_lines, group_spans, stories_spans, corrections)
    _gates_plumbing(plumbing, rows, parents)
    census = _gates_address_census(rows, content_rows)
    roster = _roster(parents, leaves)
    if residual_of(ATOM_COUNT, LEAF_COUNT, EXCLUSION_COUNT) != RESIDUAL_COUNT_GAP:
        raise RunStateError("workspace density atom ledger residual is not zero")
    if residual_of(PARSED_COUNT, PARENT_COUNT, LEAF_COUNT) != RESIDUAL_COUNT_GAP:
        raise RunStateError("workspace density parsed ledger residual is not zero")
    obligation_rows = tuple(Obligation(
        obligation_id=row["obligation_id"],
        parent_obligation=row["parent_obligation"],
        level=row["level"],
        family=row["family"],
        method=row["method"],
        line_start=row["line_start"],
        line_end=row["line_end"],
        quote=row["quote"],
        expectation=row["expectation"],
        operation=row["operation"],
        field_subject=row["field_subject"],
        basis=row["basis"],
        expected=row["expected"],
        density=row["density"],
        source_file=row["source_file"],
        source_function=row["source_function"],
        address=row["address"],
        atom_id=row["atom_id"],
        story_calls=tuple(row["story_calls"]),
        additional_story_calls=tuple(row["additional_story_calls"]),
        committed_field_obligation=row["committed_field_obligation"],
    ) for row in roster)
    basis_counts: dict[str, int] = {}
    for leaf in leaves:
        basis_counts[leaf["basis"]] = basis_counts.get(leaf["basis"], 0) + 1
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract": CONTRACT,
        "parent": PARENT,
        "case_id": CASE_ID,
        "p0_determination": dict(P0_DETERMINATION),
        "source": {
            "group_sh": {"file": f"apps/bcs/scripts/e2e-test/{GROUP_SOURCE_FILE}",
                         "sha256": GROUP_SH_SHA256},
            "stories_sh": {"file": f"apps/bcs/scripts/e2e-test/{STORIES_SOURCE_FILE}",
                           "sha256": STORIES_SH_SHA256},
            "density_oracle": {"file": "group-workspace-density-contract.json",
                               "sha256": ORACLE_SHA256, "contract": ORACLE_CONTRACT},
            "field_ledger": {"file": "bcs_e2e/workspace_field/workspace-field-obligations-contract.json",
                             "sha256": FIELD_ORACLE_SHA256, "contract": FIELD_ORACLE_CONTRACT},
            "migration_contracts": {"file": "migration-contracts.json",
                                    "sha256": MIGRATION_SHA256},
            "source_root": "e2e-test",
            "story_span": [STORY_LINE_START, STORY_LINE_END],
            "tail_span": [TAIL_LINE_START, TAIL_LINE_END],
            "group_scoped_span": [GROUP_SCOPED_FIRST, GROUP_SCOPED_LAST],
            "source_read_only": True,
        },
        "classification_rule": (
            "an atom is LIVENESS-ONLY when its check types are a subset of {status_eq, "
            "exit_zero}; every other atom is a CONTENT obligation leaf"
        ),
        "projection_rule": (
            "17 legacy WORKSPACE.* requirement rows project as parents from migration-contracts "
            "S07 onto their verbatim story step calls; 42 content atoms project as field leaves "
            "with method, line range, verbatim quote and address; 21 liveness clauses are "
            "explicit projection exclusions charged once (19 riders to the nearest following "
            "content leaf of the same roll-up parent, 2 deprecated 404 clauses to the "
            "WORKSPACE.workspace parent envelope); control-flow/fixture/guard/cleanup lines are "
            "disclosed in a 44-entry plumbing register outside every denominator"
        ),
        "accounting": {
            "parsed_obligation_count": PARSED_COUNT,
            "parent_obligation_count": PARENT_COUNT,
            "field_atomic_count": LEAF_COUNT,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
            "parsed_coverage": f"{PARSED_COUNT}/{PARSED_COUNT}",
            "atom_clauses": ATOM_COUNT,
            "atom_obligations": LEAF_COUNT,
            "atom_exclusions": EXCLUSION_COUNT,
            "atom_residual_count_gap": residual_of(ATOM_COUNT, LEAF_COUNT, EXCLUSION_COUNT),
            "leaf_family_counts": dict(LEAF_FAMILY_COUNTS),
            "exclusion_family_counts": dict(EXCLUSION_FAMILY_COUNTS),
            "basis_counts": basis_counts,
            "address_key_census": census,
            "plumbing_register_entries": PLUMBING_TOTAL,
            "plumbing_counts": dict(PLUMBING_COUNTS),
            "committed_field_cross_reference": cross_reference,
            "coverage_map_lens": {
                "case": 7,
                "gap": 42,
                "feasibility": 3,
                "score": 126,
                "snapshot_row_7": _MAP_SNAPSHOT_ROW_7,
                "rank1_basis": _MAP_RANK1_BASIS,
                "map_parsed": 59,
                "map_legacy_obligations": 17,
                "map_gap_equation": "parsed 59 - legacy 17 = 42",
                "residual_after_committed_field_slice": 16,
                "note": (
                    "snapshot-cited constants for disclosure only; the live coverage-map file is "
                    "another session's write domain and is intentionally not read at plan time"
                ),
            },
            "r8_baseline_lens": {
                "brief_green": "9/10",
                "map_row_7_baseline_cell": "PASS 10/10 叶（obl 17）",
                "group_lifecycle": "BLOCKED rc=2 (exec=null, no state dir) per the map dual note",
                "note": (
                    "the r8 green count is disclosed as a snapshot口径； the 9/10 brief value and "
                    "the map cell differ by the blocked group-lifecycle leaf and are both quoted "
                    "rather than adjudicated here"
                ),
            },
        },
        "obligations": [dict(row) for row in roster],
        "projection_exclusions": exclusions,
        "plumbing_register": plumbing,
        "legacy_corrections": list(corrections),
        "legacy_capability_gaps": list(capability_gaps),
        "predicate_vocabulary": list(oracle.get("predicate_vocabulary") or []),
        "credential_references": ["BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"],
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "input_provenance": {
            "consumed": [
                "group-workspace-density-contract.json (frozen 63-atom oracle)",
                "migration-contracts.json S07 (17 legacy requirements, steps, corrections)",
                "bcs_e2e/workspace_field/workspace-field-obligations-contract.json (cross-reference only)",
                "apps/bcs/scripts/e2e-test/group.sh",
                "apps/bcs/scripts/e2e-test/stories.sh",
            ],
            "not_consumed": (
                "workspace-migration-contract.json is an untracked parallel-session ledger of the "
                "same 43 obligations; this package never reads it and depends only on the five "
                "frozen committed inputs above"
            ),
        },
        "unlocated_source_probes": [],
        "unlocated_count": 0,
    }
    return plan, obligation_rows


def residual_of(denominator: int, left: int, right: int) -> int:
    return denominator - left - right


def atom_rows() -> tuple[dict[str, Any], ...]:
    return tuple(load_oracle()["density_requirements"])


def content_atom_rows() -> tuple[dict[str, Any], ...]:
    return tuple(row for row in atom_rows()
                 if {check.get("type") for check in row["checks"]} - LIVENESS_ONLY_TYPES)


def liveness_atom_rows() -> tuple[dict[str, Any], ...]:
    return tuple(row for row in atom_rows()
                 if {check.get("type") for check in row["checks"]} <= LIVENESS_ONLY_TYPES)


def legacy_parent_ids() -> tuple[str, ...]:
    return tuple(item["id"] for item in _legacy_requirements())


def story_step_call_lines() -> tuple[tuple[int, str], ...]:
    return tuple(_story_step_calls(stories_sh_lines()))


def exclusion_rows() -> list[dict[str, Any]]:
    return list(workspace_density_plan()["projection_exclusions"])


def plumbing_rows() -> list[dict[str, Any]]:
    return list(workspace_density_plan()["plumbing_register"])


def legacy_corrections() -> tuple[str, ...]:
    return tuple(load_migration_story()["corrections"])


def parsed_obligations() -> tuple[Obligation, ...]:
    plan, rows = _assemble()
    if len(rows) != PARSED_COUNT or len({row.obligation_id for row in rows}) != PARSED_COUNT:
        raise RunStateError("workspace density roster count or ids changed")
    if sum(row.level == "parent" for row in rows) != PARENT_COUNT:
        raise RunStateError("workspace density parent roster changed")
    if sum(row.level == "field" for row in rows) != LEAF_COUNT:
        raise RunStateError("workspace density field roster changed")
    return rows


def workspace_density_plan() -> dict[str, Any]:
    return _assemble()[0]
