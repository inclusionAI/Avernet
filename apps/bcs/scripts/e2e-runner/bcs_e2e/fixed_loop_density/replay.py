"""Credential-free offline replay for the two dynamic fixed-Loop families (E2a).

The replay consumes only an in-memory observation dictionary shaped after the
frozen owned-local executor receipts (local_fixed_loop_run.py
LOCAL_FIXED_LOOP_PASS and local_nested_fixed_loop_run.py
LOCAL_NESTED_FIXED_LOOP_PASS) and projects every roster row through its frozen
comparator.  It never launches the owned BCS runtime, never starts the story
child or the Provider/Judge mock child, never opens a CLI, a transport, a
socket or a subprocess, never resolves a credential, and never mutates a
business resource.  The two shell facades and all 59 leaf comparators are
driven exclusively by the SHA-frozen roster produced by the density contract.

Projection discipline (identical in every tier):

* roster order is the receipt order (parents, face-derived auxiliary gate,
  wrapper facade leaf, then the frozen assert atoms FLOOP.F02..F34 and
  NFLOOP.F02..NF25);
* parent and gate rows ride the parents observation map (strict "PASS" or
  absent); leaf rows ride the fields observation map (strict
  {"status", "value"} entries, "PASS" reaches the frozen comparator);
* the first FAIL cascades every later absent row to NOT_RUN with
  BLOCKED_AFTER_ASSERTION_FAILURE, never to PASS;
* malformed observations fail closed with RunStateError; absent observations
  stay honest NOT_RUN; shifted values project FAIL with OBSERVATION_MISMATCH;
* the local tier green projection mirrors the frozen completion gates
  (judge sequences, receipt partitions 4/4 per arm, output counts 6/6 or
  15/21, one-shot session posture, exact deletions, zero traffic, child exit
  0, mock child ready/exit/redaction);
* the preprod tier is an honest NOT_RUN with the overall verdict capped at
  BLOCKED_INVENTORIED: even an authorized injected observation set projects
  per-row statuses but can never convert the receipt into preprod PASS credit
  (REMOTE_UNSUPPORTED by contract for the nested family; dynamic owned-local
  runtime by design for both, so the preprod leg is never counted as a dual
  leg);
* remote_validation stays NOT_RUN and grants_release_credit stays false in
  every shape; no credential value ever appears (symbolic identities only).
"""
from __future__ import annotations

from typing import Any

from ..case_adapters import FIXED_LOOP_ADAPTER_METHOD_NAMES
from ..dynamic_family.contract import dynamic_family_contract
from ..run_state import RunStateError
from .catalog import (
    BLOCKED_AFTER_ASSERTION_FAILURE, COMMON_SH_SHA256, DENSITY_CONTRACT, DRIVER_TOKEN_ENV,
    FACE_WRAPPER_GATE, FAMILY_FIXED, FAMILY_NESTED, FIXED_CHARGED_ROW_COUNT,
    FIXED_CONTRACT_FILE, FIXED_CONTRACT_JSON_SHA256, FIXED_FINAL_OUTPUT, FIXED_LEAF_COUNT,
    FIXED_PARENT_COUNT, FIXED_PY_FILE, FIXED_PY_SHA256, FIXED_ROSTER_ROW_COUNT,
    FIXED_SH_ASSERT_LINE, FIXED_SH_FILE, FIXED_SH_SHA256, FIXED_SH_STORY_SPAN,
    FIXED_SITE_COUNT, FIXED_WRAPPER_LEAF, LOCAL_EXECUTION, NESTED_CHARGED_ROW_COUNT,
    NESTED_CONTRACT_FILE, NESTED_CONTRACT_JSON_SHA256, NESTED_LEAF_COUNT,
    NESTED_PARENT_COUNT, NESTED_PY_FILE, NESTED_PY_SHA256, NESTED_ROSTER_ROW_COUNT,
    NESTED_SH_ASSERT_LINE, NESTED_SH_STORY_SPAN, NESTED_SITE_COUNT, NESTED_WRAPPER_LEAF,
    OBSERVATION_MISMATCH, PREPROD_EXECUTION, PREPROD_INJECTED_EXECUTION,
    PROVIDER_JUDGE_MOCK_SHA256, RESIDUAL_COUNT_GAP, SCHEMA_VERSION, SOURCE_REVISION,
    STATUS_BLOCKED_INVENTORIED, STATUS_FAIL, STATUS_NOT_RUN, STATUS_PASS, STORY_FIXED,
    STORY_NESTED, SYMBOL_ARTIFACT, SYMBOL_CREDENTIAL_DOWNLINK, SYMBOL_DELIVERY_EARLY,
    SYMBOL_EXECUTION_NODE_FIRST_INNER, SYMBOL_GROUP_ECHO, SYMBOL_HUMAN_OUTPUT_FIRST_INNER,
    SYMBOL_NODE_SECOND, SYMBOL_REF_FIRST, SYMBOL_REF_SECOND, SYMBOL_SESSION_ECHO
)
from .contract import (
    DUAL_FACE_DIVERGENCE_ID, FACE_UNDERCOUNT_ID, FIXED_PREPROD_GAP_ID,
    NESTED_PREPROD_GAP_ID, fixed_loop_density_plan, fixed_loop_obligations,
    nested_fixed_loop_density_plan, nested_fixed_loop_obligations
)
from .obligations import NESTED_DELIVERY_TERMINAL_SYMBOL, NESTED_STALE_NODE_SYMBOL
from .registrations import NAMED_REGISTRATIONS

COMPARATOR_NAMES = (
    "equals", "is_none", "len_equals", "distinct", "boolean", "exit_code_equals",
    "credential_binding", "pending_render_contains", "branch_completed",
    "branch_skipped", "unreached_iteration_skips", "second_iteration_review_outcome",
    "loop_route_logical_outcome", "history_output_count", "loop_frames_equals",
    "set_equals", "depth_bounds", "delivery_ids_unchanged", "no_created_flag",
    "truthy", "envelope")

COMPARATOR_FIELDS = ("field", "comparator", "expected")

MOCK_BASIS = (
    "deterministic credential-free owned-local mirror of the frozen completion "
    "gates: fixed family judge admissions revise/approved/revise/revise with "
    "per-arm receipt partitions 4/4 and 6 output events per arm, one-shot "
    "session running, chat publication exactly once, exact deletions of 2 "
    "groups plus 1 provider bot and 0 non-target deletions, shared reset "
    "replacement verified; nested family dual story markers true with 2 "
    "observed marker checks, clean redaction gate, post-story zero traffic, "
    "child exit 0, mock child ready/exit 0 with redaction gate true, bot graph "
    "17 nodes (15 completed / 2 skipped at outer-1 and inner-3) and human "
    "graph 31 nodes (21 completed / 10 skipped at outer-3), 15+21 output "
    "events, terminal callback replay id-identical with no duplicate creation; "
    "no owned runtime, no story child, no mock child, no CLI, no transport, "
    "no network and no credential is opened by this replay")

PREPROD_BY_DESIGN = {
    "category": "REMOTE_UNSUPPORTED-by-design / dynamic owned-local runtime",
    "nested_contract_environment": {"local": "LOCAL_EXECUTOR_ONLY",
                                    "preprod": "REMOTE_UNSUPPORTED"},
    "fixed_face_mode": "DUAL",
    "fixed_face_preprod_verdict": "BLOCKED_INVENTORIED",
    "statement": (
        "the nested migration contract freezes environment "
        "{local: LOCAL_EXECUTOR_ONLY, preprod: REMOTE_UNSUPPORTED} and both "
        "stories execute on a runner-owned dynamic owned-local runtime; the "
        "preprod leg stays honest NOT_RUN and is never counted as a dual "
        "preprod leg, the DUAL face mode is executor-migration bookkeeping "
        "only, and remote_validation stays NOT_RUN with "
        "grants_release_credit false"),
    "named_gap_ids": (FIXED_PREPROD_GAP_ID, NESTED_PREPROD_GAP_ID,
                      FACE_UNDERCOUNT_ID, DUAL_FACE_DIVERGENCE_ID),
}

UPSTREAM_ROSTER = {
    "roster_verdict": "0绿/1",
    "parsed_green": 0,
    "parsed_total": 1,
    "basis": ("coverage-map committed snapshot rows before this density wave; "
              "the face parsed snapshot counts only the shell facade per "
              "story, which is the disclosed undercount"),
}

_FIXED_MOCK_FIELD_VALUES: dict[str, Any] = {
    FIXED_WRAPPER_LEAF: 0,
}
_FIXED_MOCK_FIELD_VALUES.update({
    "FLOOP.F02": True,
    "FLOOP.F03": ["first-human-pending-node-id"],
    "FLOOP.F04": ["provider-chat-send-receipt-id"],
    "FLOOP.F05": SYMBOL_CREDENTIAL_DOWNLINK, "FLOOP.F06": SYMBOL_GROUP_ECHO,
    "FLOOP.F07": SYMBOL_SESSION_ECHO,
    "FLOOP.F08": "first-human-input | second-human-input | second-editor-result",
    "FLOOP.F09": False, "FLOOP.F10": 1, "FLOOP.F11": None, "FLOOP.F12": 2,
    "FLOOP.F13": [SYMBOL_REF_FIRST, SYMBOL_REF_SECOND],
    "FLOOP.F14": "first-editor-result", "FLOOP.F15": "revise",
    "FLOOP.F16": SYMBOL_REF_SECOND,
    "FLOOP.F17": ["first-editor-result", SYMBOL_NODE_SECOND],
    "FLOOP.F18": FIXED_FINAL_OUTPUT,
    "FLOOP.F19": [["approved", "polish", "completed"],
                  ["exhausted", "rewrite", "completed"]],
    "FLOOP.F20": [["approved", "rewrite", "skipped"],
                  ["exhausted", "polish", "skipped"]],
    "FLOOP.F21": [2, ["skipped", "skipped"]],
    "FLOOP.F22": ["approved", "revise"], "FLOOP.F23": ["approved", "exhausted"],
    "FLOOP.F24": [6, 6], "FLOOP.F25": True, "FLOOP.F26": True,
    "FLOOP.F27": 0, "FLOOP.F28": 1, "FLOOP.F29": SYMBOL_ARTIFACT,
    "FLOOP.F30": True, "FLOOP.F31": True, "FLOOP.F32": True,
    "FLOOP.F33": "running", "FLOOP.F34": 1,
})

_NESTED_MOCK_FIELD_VALUES: dict[str, Any] = {
    NESTED_WRAPPER_LEAF: 0,
    "NFLOOP.F02": True,
    "NFLOOP.F03": {"before": [SYMBOL_DELIVERY_EARLY, NESTED_DELIVERY_TERMINAL_SYMBOL],
                   "after": [SYMBOL_DELIVERY_EARLY, NESTED_DELIVERY_TERMINAL_SYMBOL]},
    "NFLOOP.F04": {},
    "NFLOOP.F05": SYMBOL_DELIVERY_EARLY,
    "NFLOOP.F06": ["outer", "outer/inner"],
    "NFLOOP.F07": [f"nested-bot-graph-node-{index:02d}" for index in range(1, 18)],
    "NFLOOP.F08": [15, 2], "NFLOOP.F09": [["outer", 1], ["inner", 3]],
    "NFLOOP.F10": ["nested-human-pending-node-id"], "NFLOOP.F11": [1, 2, 3],
    "NFLOOP.F12": True,
    "NFLOOP.F13": None, "NFLOOP.F14": SYMBOL_EXECUTION_NODE_FIRST_INNER,
    "NFLOOP.F15": SYMBOL_HUMAN_OUTPUT_FIRST_INNER,
    "NFLOOP.F16": [f"first-inner-review-frame-{index:02d}" for index in range(1, 5)],
    "NFLOOP.F17": NESTED_STALE_NODE_SYMBOL,
    "NFLOOP.F18": ["outer/middle/inner", "outer/middle", "outer"],
    "NFLOOP.F19": [f"nested-human-graph-node-{index:02d}" for index in range(1, 32)],
    "NFLOOP.F20": [21, 10], "NFLOOP.F21": True,
    "NFLOOP.F22": [15, 21], "NFLOOP.F23": ["nested-output-event-match-id"],
    "NFLOOP.F24": True, "NFLOOP.F25": False,
}

FAMILY_MOCK_FIELD_VALUES = {
    FAMILY_FIXED: dict(_FIXED_MOCK_FIELD_VALUES),
    FAMILY_NESTED: dict(_NESTED_MOCK_FIELD_VALUES),
}

_LOCAL_CLASSIFICATION = (
    "owned-local credential-free offline replay mirroring the frozen "
    "completion gates of the two owned-local executors; no transport, no CLI, "
    "no network, no subprocess, no credential and no release credit")
_PREPROD_CLASSIFICATION = (
    "preprod density replay is not executed by this offline package: the "
    "nested family is contractually REMOTE_UNSUPPORTED and both families use "
    "a dynamic owned-local runtime, so the preprod leg stays honest NOT_RUN "
    "and is never counted as a dual preprod leg")
_INJECTED_CLASSIFICATION = (
    "authorized injected observation projected per row; the overall verdict "
    "stays capped at BLOCKED_INVENTORIED and no preprod PASS credit is granted")
_ABSENT_CLASSIFICATION = ("observation absent from the observation set; the "
                          "row stays NOT_RUN and never defaults to PASS")
_MISMATCH_CLASSIFICATION = ("observation mismatch; fail-close, never "
                            "normalized to a PASS")
PREPROD_EXECUTION_NOT_RUN = "PREPROD_EXECUTION_NOT_RUN"

_CASCADE_CLASSIFICATION = ("observation absent after an earlier assertion "
                           "failure; honest NOT_RUN cascade, never a PASS")


def mock_observations() -> dict[str, Any]:
    """Return the deterministic credential-free local replay fixture."""
    fixed_rows = fixed_loop_obligations()
    nested_rows = nested_fixed_loop_obligations()
    observations: dict[str, Any] = {}
    for family, rows, values in ((FAMILY_FIXED, fixed_rows, _FIXED_MOCK_FIELD_VALUES),
                                 (FAMILY_NESTED, nested_rows, _NESTED_MOCK_FIELD_VALUES)):
        parents: dict[str, Any] = {}
        fields: dict[str, Any] = {}
        for row in rows:
            if row.level == "PARENT":
                parents[row.obligation_id] = STATUS_PASS
            else:
                fields[row.obligation_id] = {"status": STATUS_PASS,
                                             "value": values[row.obligation_id]}
        observations[family] = {"parents": parents, "fields": fields}
    return observations


def compare_observation(comparator: str, observed: Any, expected: Any) -> bool:
    """Evaluate one frozen comparator; unknown comparators fail closed."""
    if comparator == "equals":
        return type(observed) is type(expected) and observed == expected
    if comparator == "is_none":
        return observed is None and expected is None
    if comparator == "len_equals":
        return (isinstance(observed, (str, list, tuple, dict))
                and isinstance(expected, int) and len(observed) == expected)
    if comparator == "distinct":
        return (isinstance(observed, (list, tuple))
                and isinstance(expected, (list, tuple))
                and len(observed) == len(expected)
                and len(set(observed)) == len(observed)
                and set(observed) == set(expected))
    if comparator == "boolean":
        return type(observed) is bool and observed == expected
    if comparator == "exit_code_equals":
        return type(observed) is int and observed == expected
    if comparator == "credential_binding":
        return type(observed) is str and observed == expected
    if comparator == "pending_render_contains":
        if isinstance(observed, str):
            return all(member in observed for member in expected)
        return (isinstance(observed, (list, tuple))
                and all(member in observed for member in expected))
    if comparator in ("branch_completed", "branch_skipped"):
        return _sorted_triple_table(observed, expected)
    if comparator == "unreached_iteration_skips":
        return (isinstance(observed, (list, tuple)) and len(observed) == 2
                and type(observed[0]) is int
                and observed[0] == expected[0]
                and list(observed[1]) == list(expected[1])
                and len(observed[1]) == observed[0]
                and all(status == "skipped" for status in observed[1]))
    if comparator in ("second_iteration_review_outcome",
                      "loop_route_logical_outcome"):
        return (isinstance(observed, (list, tuple)) and len(observed) == 2
                and sorted(observed) == sorted(expected))
    if comparator == "history_output_count":
        return (isinstance(observed, (list, tuple)) and len(observed) == 2
                and type(observed[0]) is int and type(observed[1]) is int
                and list(observed) == list(expected))
    if comparator == "loop_frames_equals":
        return (isinstance(observed, (list, tuple)) and len(observed) == len(expected)
                and all(len(row) == 2 and type(row[0]) is str and type(row[1]) is int
                        for row in observed)
                and [list(row) for row in observed] == [list(row) for row in expected])
    if comparator == "set_equals":
        return (isinstance(observed, (list, tuple, set))
                and len(observed) == len(expected)
                and set(observed) == set(expected))
    if comparator == "depth_bounds":
        return (isinstance(observed, (list, tuple)) and observed
                and all(type(depth) is int for depth in observed)
                and all(expected[0] <= depth <= expected[1] for depth in observed))
    if comparator == "delivery_ids_unchanged":
        return (isinstance(observed, dict)
                and isinstance(observed.get("before"), list)
                and isinstance(observed.get("after"), list)
                and observed["before"] and observed["after"]
                and observed["before"] == observed["after"]
                and observed["before"] == list(expected["before"])
                and observed["after"] == list(expected["after"]))
    if comparator == "no_created_flag":
        return isinstance(observed, dict) and "created" not in observed
    if comparator == "truthy":
        return bool(observed) is True and observed == expected
    if comparator == "envelope":
        return observed == STATUS_PASS
    raise RunStateError(f"fixed-loop density replay comparator is not frozen: {comparator}")


def _sorted_triple_table(observed: Any, expected: Any) -> bool:
    if not (isinstance(observed, (list, tuple))
            and isinstance(expected, (list, tuple))
            and len(observed) == len(expected)):
        return False
    try:
        normalized_observed = sorted(tuple(row) for row in observed)
        normalized_expected = sorted(tuple(row) for row in expected)
    except TypeError:
        return False
    return (normalized_observed == normalized_expected
            and all(len(row) == 3 for row in normalized_observed)
            and all(type(member) is str for row in normalized_observed
                    for member in row))


_EXECUTOR_BINDING_EXTENSIONS = {
    FAMILY_FIXED: {"adapter_seams": list(FIXED_LOOP_ADAPTER_METHOD_NAMES)},
    FAMILY_NESTED: {"black_box_child_keys": [
        "child_exit_code", "mock_child_ready", "mock_child_exit_code",
        "mock_child_redaction_gate", "required_assertion_count",
        "assertions_all_pass", "story_markers_observed", "observed_marker_checks",
        "zero_traffic", "mock_child_control_planes", "resource_disposition",
        "credential_memory"]},
}


def _requested_families(family: str) -> tuple[str, ...]:
    if family == "both":
        return (FAMILY_FIXED, FAMILY_NESTED)
    if family in (FAMILY_FIXED, FAMILY_NESTED):
        return (family,)
    raise RunStateError("fixed-loop density replay family must be fixed_loop, "
                        "nested_fixed_loop or both")


def _validate_layers(observations: dict[str, Any],
                     requested: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    if not isinstance(observations, dict):
        raise RunStateError("fixed-loop density replay observations must be an object")
    known = {FAMILY_FIXED, FAMILY_NESTED}
    unknown = sorted(set(observations) - known)
    if unknown:
        raise RunStateError(
            f"fixed-loop density replay observation layer has unknown families: {unknown}")
    for family in requested:
        if family not in observations:
            raise RunStateError(
                f"fixed-loop density replay observations miss the requested family: {family}")
    layers: dict[str, dict[str, Any]] = {}
    for family in requested:
        layer = observations[family]
        if not isinstance(layer, dict) or not set(layer) <= {"parents", "fields"}:
            raise RunStateError(
                f"fixed-loop density replay family layer must carry only parents and fields: "
                f"{family}")
        parents = layer.get("parents", {})
        fields = layer.get("fields", {})
        if not isinstance(parents, dict) or not isinstance(fields, dict):
            raise RunStateError(
                f"fixed-loop density replay parents and fields must be objects: {family}")
        for parent_id, entry in parents.items():
            if entry is not None and not isinstance(entry, str):
                raise RunStateError(
                    f"fixed-loop density replay parent entry must be a string or null: "
                    f"{family} {parent_id}")
        for leaf_id, entry in fields.items():
            if not isinstance(entry, dict) or set(entry) != {"status", "value"}:
                raise RunStateError(
                    f"fixed-loop density replay leaf entry must carry exactly status and "
                    f"value keys: {family} {leaf_id}")
        layers[family] = {"parents": parents, "fields": fields}
    return layers


def _parent_status(entry: Any) -> tuple[str, str | None]:
    if entry is None:
        return STATUS_NOT_RUN, None
    if entry == STATUS_PASS:
        return STATUS_PASS, None
    return STATUS_FAIL, OBSERVATION_MISMATCH


def _leaf_status(entry: dict[str, Any], comparator: str,
                 expected: Any) -> tuple[str, str | None]:
    if entry["status"] != STATUS_PASS:
        return STATUS_FAIL, OBSERVATION_MISMATCH
    if compare_observation(comparator, entry["value"], expected):
        return STATUS_PASS, None
    return STATUS_FAIL, OBSERVATION_MISMATCH


def _evidence_event(row_level: str, is_gate: bool, status: str, tier: str,
                    injected: bool, failed_already: bool) -> str:
    if tier == "preprod" and not injected:
        return "preprod_story_not_executed_offline"
    if status == STATUS_FAIL:
        return "observation_mismatch"
    if status == STATUS_NOT_RUN:
        return "blocked_after_assertion_failure" if failed_already else "observation_absent"
    if row_level == "PARENT":
        return "face_wrapper_gate_mirror" if is_gate else "migration_contract_envelope_mirror"
    return "owned_local_story_site_mirror"


def _classification(tier: str, injected: bool, status: str,
                    cascade: bool) -> str:
    if status == STATUS_FAIL:
        return _MISMATCH_CLASSIFICATION
    if status == STATUS_NOT_RUN:
        if tier == "preprod" and not injected:
            return _PREPROD_CLASSIFICATION
        return _CASCADE_CLASSIFICATION if cascade else _ABSENT_CLASSIFICATION
    if tier == "preprod":
        return _INJECTED_CLASSIFICATION
    return _LOCAL_CLASSIFICATION


def _family_receipt(family: str, rows: tuple, layer: dict[str, Any] | None,
                    tier: str, injected: bool) -> tuple[list[dict[str, Any]], dict[str, int]]:
    projected: list[dict[str, Any]] = []
    failed_seen = False
    force_not_run = tier == "preprod" and not injected
    for row in rows:
        is_gate = (row.envelope_source == FACE_WRAPPER_GATE and row.level == "PARENT")
        observed: Any = None
        status = STATUS_NOT_RUN
        error_type: str | None = None
        if force_not_run:
            error_type = PREPROD_EXECUTION_NOT_RUN
        elif layer is not None:
            if row.level == "PARENT":
                entry = layer["parents"].get(row.obligation_id)
                status, error_type = _parent_status(entry)
                observed = entry
            else:
                entry = layer["fields"].get(row.obligation_id)
                if entry is not None:
                    status, error_type = _leaf_status(entry, row.comparator, row.expected)
                    observed = entry["value"]
        if status == STATUS_FAIL:
            failed_seen = True
        elif (status == STATUS_NOT_RUN and failed_seen
                and error_type not in (PREPROD_EXECUTION_NOT_RUN,)):
            error_type = BLOCKED_AFTER_ASSERTION_FAILURE
        cascade = error_type == BLOCKED_AFTER_ASSERTION_FAILURE
        projected.append({
            **row.as_dict(),
            "family": family, "status": status, "error_type": error_type,
            "observed": observed,
            "evidence_event": _evidence_event(row.level, is_gate, status, tier,
                                              injected, cascade),
            "local_classification": _classification(tier, injected, status, cascade),
        })
    counts = {STATUS_PASS: sum(1 for item in projected if item["status"] == STATUS_PASS),
              STATUS_FAIL: sum(1 for item in projected if item["status"] == STATUS_FAIL),
              STATUS_NOT_RUN: sum(1 for item in projected if item["status"] == STATUS_NOT_RUN)}
    return projected, counts


def _overall_status(counts: dict[str, int], total: int, tier: str) -> str:
    if tier == "preprod":
        return STATUS_BLOCKED_INVENTORIED
    if counts[STATUS_PASS] == total:
        return STATUS_PASS
    if counts[STATUS_FAIL]:
        return STATUS_FAIL
    return STATUS_BLOCKED_INVENTORIED


def project(observations: dict[str, Any] | None = None, *, tier: str = "local",
            family: str = "both") -> dict[str, Any]:
    """Project in-memory observations into the honest 85-row density receipt."""
    if tier not in ("local", "preprod"):
        raise RunStateError("fixed-loop density replay tier must be local or preprod")
    requested = _requested_families(family)
    injected_preprod = tier == "preprod" and observations is not None
    if observations is None:
        observations = {} if tier == "preprod" else mock_observations()
    force_not_run = tier == "preprod" and not injected_preprod
    layers = ({name: None for name in requested} if force_not_run
              else _validate_layers(observations, requested))

    fixed_rows = fixed_loop_obligations()
    nested_rows = nested_fixed_loop_obligations()
    rosters = {FAMILY_FIXED: fixed_rows, FAMILY_NESTED: nested_rows}
    expected_rows = {FAMILY_FIXED: FIXED_ROSTER_ROW_COUNT, FAMILY_NESTED: NESTED_ROSTER_ROW_COUNT}
    expected_charged = {FAMILY_FIXED: FIXED_CHARGED_ROW_COUNT,
                        FAMILY_NESTED: NESTED_CHARGED_ROW_COUNT}
    row_calibers = {FAMILY_FIXED: (FIXED_PARENT_COUNT, FIXED_LEAF_COUNT, FIXED_SITE_COUNT),
                    FAMILY_NESTED: (NESTED_PARENT_COUNT, NESTED_LEAF_COUNT, NESTED_SITE_COUNT)}
    for name in requested:
        if len(rosters[name]) != expected_rows[name]:
            raise RunStateError(f"fixed-loop density replay roster caliber changed: {name}")

    family_blocks: dict[str, Any] = {}
    obligations: list[dict[str, Any]] = []
    dynamic_fixed = dynamic_family_contract(FAMILY_FIXED)
    dynamic_nested = dynamic_family_contract(FAMILY_NESTED)
    dynamic = {FAMILY_FIXED: dynamic_fixed, FAMILY_NESTED: dynamic_nested}
    plans = {FAMILY_FIXED: fixed_loop_density_plan(),
             FAMILY_NESTED: nested_fixed_loop_density_plan()}
    for name in requested:
        projected, counts = _family_receipt(name, rosters[name], layers[name], tier,
                                            injected_preprod)
        if sum(counts.values()) != len(rosters[name]):
            raise RunStateError(f"fixed-loop density replay coverage is not total: {name}")
        status = _overall_status(counts, len(rosters[name]), tier)
        plan = plans[name]
        binding = dict(plan["executor_binding"])
        binding["command"] = dynamic[name].command
        binding["local_executor"] = dynamic[name].local_executor
        binding.update(_EXECUTOR_BINDING_EXTENSIONS[name])
        family_blocks[name] = {
            "status": status, "counts": counts, "roster_rows": len(rosters[name]),
            "charged_row_count": expected_charged[name],
            "contract_parents": row_calibers[name][0],
            "parsed_sites": row_calibers[name][2], "residual_count_gap": RESIDUAL_COUNT_GAP,
            "executor_binding": binding,
            "wrapper_facade": plan["wrapper_facade"],
            "named_registrations": list(plan["named_registrations"]),
            "preprod": {
                "status": STATUS_NOT_RUN,
                "named_gap_ids": list(plan["named_registrations"]),
                "by_design": PREPROD_BY_DESIGN["statement"]},
        }
        obligations.extend(projected)

    counts = {STATUS_PASS: sum(block["counts"][STATUS_PASS] for block in family_blocks.values()),
              STATUS_FAIL: sum(block["counts"][STATUS_FAIL] for block in family_blocks.values()),
              STATUS_NOT_RUN: sum(block["counts"][STATUS_NOT_RUN] for block in family_blocks.values())}
    total = len(obligations)
    if sum(counts.values()) != total:
        raise RunStateError("fixed-loop density replay coverage is not total")
    charged_row_count = sum(1 for row in obligations if row["charged"])
    if charged_row_count != sum(expected_charged[name] for name in requested):
        raise RunStateError("fixed-loop density replay charged caliber changed")

    applies_to: dict[str, int] = {registration_id: 0
                                  for registration_id in NAMED_REGISTRATIONS}
    for row in obligations:
        if row["charged"]:
            for registration_id in row["named_registrations"]:
                applies_to[registration_id] += 1
    for registration_id in (FACE_UNDERCOUNT_ID, DUAL_FACE_DIVERGENCE_ID):
        if applies_to[registration_id] != charged_row_count:
            raise RunStateError("fixed-loop density replay named registration scope broke")
    if (FAMILY_FIXED in requested
            and applies_to[FIXED_PREPROD_GAP_ID] != FIXED_CHARGED_ROW_COUNT):
        raise RunStateError("fixed-loop density named gap scope broke")
    if (FAMILY_NESTED in requested
            and applies_to[NESTED_PREPROD_GAP_ID] != NESTED_CHARGED_ROW_COUNT):
        raise RunStateError("nested fixed-loop density named gap scope broke")

    return {
        "schema_version": SCHEMA_VERSION, "contract": DENSITY_CONTRACT,
        "tier": tier, "family": family, "status": _overall_status(counts, total, tier),
        "counts": counts, "charged_row_count": charged_row_count,
        "parsed_roster_rows": total, "residual_count_gap": RESIDUAL_COUNT_GAP,
        "families": family_blocks, "obligations": obligations,
        "named_registrations": [
            {**NAMED_REGISTRATIONS[registration_id],
             "applies_to_charged_rows": applies_to[registration_id]}
            for registration_id in (FACE_UNDERCOUNT_ID, DUAL_FACE_DIVERGENCE_ID,
                                    FIXED_PREPROD_GAP_ID, NESTED_PREPROD_GAP_ID)],
        "execution": (LOCAL_EXECUTION if tier == "local"
                      else (PREPROD_INJECTED_EXECUTION if injected_preprod
                            else PREPROD_EXECUTION)),
        "transport_admission": (
            "in-memory observations only; no owned BCS runtime, no story child, no "
            "mock child, no CLI, no transport, no socket, no subprocess, no network "
            "connection, and no credential is opened or resolved by this replay"),
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "source_freeze": {
            "shell_story": {"file": FIXED_SH_FILE, "sha256": FIXED_SH_SHA256,
                            "story_spans": {STORY_FIXED: list(FIXED_SH_STORY_SPAN),
                                            STORY_NESTED: list(NESTED_SH_STORY_SPAN)},
                            "facade_assert_lines": {STORY_FIXED: FIXED_SH_ASSERT_LINE,
                                                    STORY_NESTED: NESTED_SH_ASSERT_LINE}},
            "python_stories": {
                FAMILY_FIXED: {"file": FIXED_PY_FILE, "sha256": FIXED_PY_SHA256},
                FAMILY_NESTED: {"file": NESTED_PY_FILE, "sha256": NESTED_PY_SHA256}},
            "migration_contracts": {
                FAMILY_FIXED: {"file": FIXED_CONTRACT_FILE,
                               "sha256": FIXED_CONTRACT_JSON_SHA256},
                FAMILY_NESTED: {"file": NESTED_CONTRACT_FILE,
                                "sha256": NESTED_CONTRACT_JSON_SHA256}},
            "source_revision": SOURCE_REVISION,
            "supporting_freezes": {"common.sh": COMMON_SH_SHA256,
                                   "http_provider_judge_mock.py": PROVIDER_JUDGE_MOCK_SHA256},
        },
        "upstream_roster": dict(UPSTREAM_ROSTER),
        "preprod_by_design": PREPROD_BY_DESIGN,
        "credential_references": [DRIVER_TOKEN_ENV],
    }
