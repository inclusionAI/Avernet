"""Source-anchored density contract for the dynamic fixed-Loop families (E2a).

Two frozen upstream dynamic python stories are obligationized here without
re-declaring any migrated requirement:

- story_fixed_loop_human_editor_workflow (row #18): the shell facade in
  fixed_loop_story.sh plus the 66-site python ledger of fixed_loop_story.py
  (33 assert leaves + 33 transport checkpoints) = 67 parsed sites, projected
  as 8 migration-contract parent envelopes plus 34 charged leaves
  (1 wrapper facade leaf + 33 assert leaves) = 42 charged rows.
- story_nested_fixed_loop_workflows (row #19): the shell facade plus the
  46-site python ledger of nested_fixed_loop_story.py (24 assert leaves +
  22 transport checkpoints) = 47 parsed sites, projected as 16 migration-
  contract parent envelopes plus 25 charged leaves = 41 charged rows.

Family caliber: 59 leaves, 24 contract parents, 83 charged rows, 114 parsed
sites (112 executor-ledger python sites + 2 shell facades), residual gap 0
under both the site caliber and the row caliber.

Every quote in this package is assembled at call time from SHA-frozen disk
bytes; no upstream story byte is transcribed into this source, so the
credential-shaped bearer prefix on the Provider downlink check stays a
disk-assembled quote and never appears as a source literal.  Upstream colors
are NOT_ROSTERED (no per-check poolab receipt retained for the dynamic
owned-local families; negative-evidence discipline).  Nothing here fabricates
a PASS: the owned-local tier is an offline projection of the frozen owned-local
executor completion gates, the preprod tier is an honest NOT_RUN with the
REMOTE_UNSUPPORTED-by-design gaps registered by name, and
grants_release_credit stays false in every tier.

The frozen-anchor catalog (file table, SHA-256 freezes, calibers,
symbolic identities, Obligation/SiteSpan dataclasses and the
SHA-verified byte loaders) lives in the sibling catalog module and is
re-exported below unchanged; this module keeps validation, gating,
envelope assembly and plan projection.  The split is lossless: every
frozen value, caliber and anchored quote is byte-identical, and the
__all__ surface below is unchanged."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..dynamic_family.contract import dynamic_family_contract
from ..local_fixed_loop_contract import fixed_loop_source_binding
from ..local_nested_fixed_loop_contract import nested_fixed_loop_source_binding
from ..run_state import RunStateError
from .catalog import (  # noqa: F401  (re-exported frozen-anchor surface)
    BLOCKED_AFTER_ASSERTION_FAILURE, B_UPSTREAM_NOT_ROSTERED, COMMON_SH_SHA256,
    DENSITY_CONTRACT, DRIVER_TOKEN_ENV, E2E_TEST_DIR, FACE_WRAPPER_GATE,
    FAMILY_CHARGED_ROW_COUNT, FAMILY_FIXED, FAMILY_LEAF_COUNT, FAMILY_LEDGER_SITE_COUNT,
    FAMILY_NESTED, FAMILY_PARENT_COUNT, FAMILY_ROSTER_ROW_COUNT, FAMILY_SITE_COUNT,
    FIXED_ASSERT_SITE_COUNT, FIXED_AUX_ENVELOPE_COUNT, FIXED_CHARGED_ROW_COUNT,
    FIXED_CONTRACT_ENVELOPE, FIXED_CONTRACT_FILE, FIXED_CONTRACT_JSON_PATH,
    FIXED_CONTRACT_JSON_SHA256, FIXED_FACE_GAP, FIXED_FACE_OBLIGATIONS, FIXED_FACE_PARSED,
    FIXED_FINAL_OUTPUT, FIXED_FIRST_EDITOR_OUTPUT, FIXED_FIRST_HUMAN_INPUT,
    FIXED_JUDGE_OUTCOMES, FIXED_LEAF_COUNT, FIXED_LEDGER_SITE_COUNT, FIXED_PARENT_COUNT,
    FIXED_PREFIX, FIXED_PY_FILE, FIXED_PY_PATH, FIXED_PY_SHA256, FIXED_ROSTER_ROW_COUNT,
    FIXED_SECOND_EDITOR_OUTPUT, FIXED_SECOND_HUMAN_INPUT, FIXED_SH_ASSERT_LINE,
    FIXED_SH_FILE, FIXED_SH_PATH, FIXED_SH_SHA256, FIXED_SH_STORY_SPAN, FIXED_SITE_COUNT,
    FIXED_TRANSPORT_SITE_COUNT, FIXED_WRAPPER_LEAF, GATE_ID_FIXED, GATE_ID_NESTED,
    LOCAL_EXECUTION, NESTED_ASSERT_SITE_COUNT, NESTED_AUX_ENVELOPE_COUNT,
    NESTED_BOT_JUDGE_OUTCOMES, NESTED_BOT_SKIPPED_FRAMES, NESTED_BOT_STEPS,
    NESTED_CHARGED_ROW_COUNT, NESTED_CONTRACT_ENVELOPE, NESTED_CONTRACT_FILE,
    NESTED_CONTRACT_JSON_PATH, NESTED_CONTRACT_JSON_SHA256, NESTED_FACE_GAP,
    NESTED_FACE_OBLIGATIONS, NESTED_FACE_PARSED, NESTED_HUMAN_JUDGE_OUTCOMES,
    NESTED_LEAF_COUNT, NESTED_LEDGER_SITE_COUNT, NESTED_PARENT_COUNT, NESTED_PREFIX,
    NESTED_PY_FILE, NESTED_PY_PATH, NESTED_PY_SHA256, NESTED_ROSTER_ROW_COUNT,
    NESTED_SH_ASSERT_LINE, NESTED_SH_STORY_SPAN, NESTED_SITE_COUNT,
    NESTED_TRANSPORT_SITE_COUNT, NESTED_WRAPPER_LEAF, OBSERVATION_MISMATCH, Obligation,
    PREPROD_EXECUTION, PREPROD_INJECTED_EXECUTION, PROVIDER_JUDGE_MOCK_SHA256,
    RESIDUAL_COUNT_GAP, RUNNER_ROOT, SCHEMA_VERSION, SHARED_CONTROL_RESETS, SOURCE_REVISION,
    STATUS_BLOCKED_INVENTORIED, STATUS_FAIL, STATUS_NOT_RUN, STATUS_PASS, STORY_FIXED,
    STORY_NESTED, SYMBOL_ARTIFACT, SYMBOL_CREDENTIAL_DOWNLINK, SYMBOL_DELIVERY_EARLY,
    SYMBOL_EXECUTION_NODE_FIRST_INNER, SYMBOL_GROUP_ECHO, SYMBOL_HUMAN_OUTPUT_FIRST_INNER,
    SYMBOL_NODE_SECOND, SYMBOL_REF_FIRST, SYMBOL_REF_SECOND, SYMBOL_SESSION_ECHO, SiteSpan,
    UPSTREAM_NOT_ROSTERED, _frozen_lines, _json_requirement_span, _sh_facade_assert_message,
    _sh_story_function_span, _span_lines, site_records, site_span_index, story_lines,
    wrapper_sh_story_lines
)
from .registrations import (
    DUAL_FACE_DIVERGENCE_ID, FACE_UNDERCOUNT_ID, FAMILY_TOKEN,
    FIXED_COVERAGE_MAP_ROW_NOTE_PROPOSAL, FIXED_ROW_NOTE_PROPOSAL, FIXED_ROW_TOKEN,
    FIXED_PREPROD_GAP_ID, NAMED_REGISTRATIONS, NESTED_COVERAGE_MAP_ROW_NOTE_PROPOSAL,
    NESTED_ROW_NOTE_PROPOSAL, NESTED_ROW_TOKEN, NESTED_PREPROD_GAP_ID
)

def _validate_family_binding(family: str) -> dict:
    if family == FAMILY_FIXED:
        return fixed_loop_source_binding()
    return nested_fixed_loop_source_binding()


def _family_contract(family: str):
    return dynamic_family_contract(family)


def _family_rows(family: str) -> list[dict]:
    binding = _validate_family_binding(family)
    return [dict(row) for row in binding["requirements"]]


def _owned_sites(family: str, requirement_id: str, kind: str) -> tuple[str, ...]:
    """Return the family ledger sites of one kind owned by one requirement.

    Multiplicity is preserved: a site shared by several requirements rides
    each owning envelope once; the family-wide union is asserted by the
    roster builders, never silently deduplicated here.
    """
    ledger = site_span_index(family)
    owned = tuple(
        site_id for site_id, owners in _family_contract(family).assertion_requirement_ids.items()
        if requirement_id in owners and ledger[site_id].kind == kind)
    if any(site not in ledger for site in owned):
        raise RunStateError("fixed-loop density site ownership left the frozen ledger")
    return owned


def _owned_transport_sites(family: str, requirement_id: str) -> tuple[str, ...]:
    return _owned_sites(family, requirement_id, "transport")


def _owned_assert_sites(family: str, requirement_id: str) -> tuple[str, ...]:
    return _owned_sites(family, requirement_id, "assert")


def build_fixed_parent_envelopes() -> list[dict[str, Any]]:
    """Build the 8 FIXED_LOOP.* parent envelopes verbatim from the frozen JSON."""
    rows = _family_rows(FAMILY_FIXED)
    family = _family_contract(FAMILY_FIXED)
    json_text = FIXED_CONTRACT_JSON_PATH.read_text(encoding="utf-8")
    json_lines = json_text.splitlines()
    if len(rows) != FIXED_PARENT_COUNT:
        raise RunStateError("fixed-Loop parent envelope count changed")
    if tuple(row["id"] for row in rows) != tuple(family.requirement_ids):
        raise RunStateError("fixed-Loop parent requirement order changed")
    envelopes = []
    for row in rows:
        span = _json_requirement_span(json_text, row["id"])
        offsets = _span_lines(json_text, span[0], span[1])
        quote = "\n".join(json_lines[offsets[0] - 1:offsets[1]])
        payload = quote.rstrip()
        if payload.endswith(","):
            payload = payload[:-1].rstrip()
        if json.loads(payload).get("id") != row["id"]:
            raise RunStateError(f"fixed-Loop parent envelope quote drifted: {row['id']}")
        envelopes.append({
            "obligation_id": row["id"],
            "envelope_source": FIXED_CONTRACT_ENVELOPE,
            "expectation": row["expectation"],
            "method": row["method"],
            "path": row["path"],
            "actor": row["actor"],
            "kind": row["kind"],
            "line_start": offsets[0],
            "line_end": offsets[1],
            "quote": quote,
            "source_file": FIXED_CONTRACT_FILE,
            "internal_checkpoints": _owned_transport_sites(FAMILY_FIXED, row["id"]),
            "internal_assert_sites": _owned_assert_sites(FAMILY_FIXED, row["id"]),
        })
    return envelopes


def build_nested_parent_envelopes() -> list[dict[str, Any]]:
    """Build the 16 NESTED-LOOP.* parent envelopes from the frozen contract."""
    rows = _family_rows(FAMILY_NESTED)
    family = _family_contract(FAMILY_NESTED)
    binding = nested_fixed_loop_source_binding()
    if len(rows) != NESTED_PARENT_COUNT:
        raise RunStateError("nested fixed-Loop parent envelope count changed")
    if tuple(row["id"] for row in rows) != tuple(family.requirement_ids):
        raise RunStateError("nested fixed-Loop parent requirement order changed")
    story_lines = _frozen_lines(NESTED_PY_PATH, NESTED_PY_SHA256, NESTED_PY_FILE)
    envelopes = []
    for row in rows:
        low, high = row["story_lines"]
        if not (1 <= low <= high <= len(story_lines)):
            raise RunStateError(f"nested fixed-Loop parent story span drifted: {row['id']}")
        quote = "\n".join(story_lines[low - 1:high])
        envelopes.append({
            "obligation_id": row["id"],
            "envelope_source": NESTED_CONTRACT_ENVELOPE,
            "expectation": row["expectation"],
            "method": "story_span",
            "path": f"nested_fixed_loop_story.py:{low}-{high}",
            "actor": "undeclared-in-nested-contract",
            "kind": "requirement_row",
            "line_start": low,
            "line_end": high,
            "quote": quote,
            "source_file": NESTED_PY_FILE,
            "story_lines": (low, high),
            "markers": tuple(row["markers"]),
            "internal_checkpoints": _owned_transport_sites(FAMILY_NESTED, row["id"]),
            "internal_assert_sites": _owned_assert_sites(FAMILY_NESTED, row["id"]),
        })
    if binding["environment"]["preprod"] != "REMOTE_UNSUPPORTED":
        raise RunStateError("nested fixed-Loop preprod contract posture changed")
    return envelopes


def build_wrapper_gate_envelopes() -> list[dict[str, Any]]:
    """Build the two face-derived auxiliary gate envelopes (disclosed, uncharged)."""
    sh_lines = _frozen_lines(FIXED_SH_PATH, FIXED_SH_SHA256, FIXED_SH_FILE)
    envelopes = []
    for story, family, span, assert_line in (
            (STORY_FIXED, FAMILY_FIXED, FIXED_SH_STORY_SPAN, FIXED_SH_ASSERT_LINE),
            (STORY_NESTED, FAMILY_NESTED, NESTED_SH_STORY_SPAN, NESTED_SH_ASSERT_LINE)):
        computed = _sh_story_function_span(sh_lines, story)
        if computed != span:
            raise RunStateError(f"shell facade story span constant drifted: {story}")
        if not (span[0] <= assert_line <= span[1]):
            raise RunStateError(f"shell facade assert line escaped its story span: {story}")
        message = _sh_facade_assert_message(sh_lines, assert_line)
        gate_id = GATE_ID_FIXED if family == FAMILY_FIXED else GATE_ID_NESTED
        envelopes.append({
            "obligation_id": gate_id,
            "family": family,
            "envelope_source": FACE_WRAPPER_GATE,
            "expectation": (
                f"Shell facade {story} exits 0 after its dual-arm story run and cleanup "
                f"({message}); face-derived auxiliary envelope, not a migration-contract "
                "obligation, and never counted in the charged parent caliber"),
            "method": "assert_eq",
            "path": f"fixed_loop_story.sh:{span[0]}-{span[1]}",
            "actor": "LOCAL_CONTROL",
            "kind": "face_wrapper_gate",
            "line_start": span[0],
            "line_end": span[1],
            "quote": "\n".join(sh_lines[span[0] - 1:span[1]]),
            "source_file": FIXED_SH_FILE,
            "facade_assert_line": assert_line,
            "facade_assert_message": message,
        })
    return envelopes


def family_parent_envelopes(family: str) -> list[dict[str, Any]]:
    if family == FAMILY_FIXED:
        return build_fixed_parent_envelopes()
    return build_nested_parent_envelopes()


def wrapper_gate_envelope(family: str) -> dict[str, Any]:
    for envelope in build_wrapper_gate_envelopes():
        if envelope["family"] == family:
            return envelope
    raise RunStateError("wrapper gate envelope missing")


def fixed_loop_obligations() -> tuple[Obligation, ...]:
    from .obligations import build_fixed_loop_roster  # noqa: PLC0415
    return build_fixed_loop_roster()


def nested_fixed_loop_obligations() -> tuple[Obligation, ...]:
    from .obligations import build_nested_loop_roster  # noqa: PLC0415
    return build_nested_loop_roster()


def obligations() -> tuple[Obligation, ...]:
    """Return the full 85-row roster with every global invariant enforced."""
    rows = fixed_loop_obligations() + nested_fixed_loop_obligations()
    if len(rows) != FAMILY_ROSTER_ROW_COUNT:
        raise RunStateError("fixed-loop density family roster caliber changed")
    if len({row.obligation_id for row in rows}) != FAMILY_ROSTER_ROW_COUNT:
        raise RunStateError("fixed-loop density roster ids are not unique")
    charged = [row for row in rows if row.charged]
    if len(charged) != FAMILY_CHARGED_ROW_COUNT:
        raise RunStateError("fixed-loop density charged row caliber changed")
    fixed_rows = [row for row in rows if row.story == STORY_FIXED]
    nested_rows = [row for row in rows if row.story == STORY_NESTED]
    if len(fixed_rows) != FIXED_ROSTER_ROW_COUNT or len(nested_rows) != NESTED_ROSTER_ROW_COUNT:
        raise RunStateError("fixed-loop density per-story roster caliber changed")
    for row in rows:
        for registration in row.named_registrations:
            if registration not in NAMED_REGISTRATIONS:
                raise RunStateError(f"fixed-loop density named registration unknown: {row.obligation_id}")
    _validate_quotes(rows)
    return rows


def _validate_quotes(rows: tuple[Obligation, ...]) -> None:
    sh_lines = _frozen_lines(FIXED_SH_PATH, FIXED_SH_SHA256, FIXED_SH_FILE)
    fixed_lines = _frozen_lines(FIXED_PY_PATH, FIXED_PY_SHA256, FIXED_PY_FILE)
    nested_lines = _frozen_lines(NESTED_PY_PATH, NESTED_PY_SHA256, NESTED_PY_FILE)
    fixed_json = FIXED_CONTRACT_JSON_PATH.read_text(encoding="utf-8")
    nested_json = NESTED_CONTRACT_JSON_PATH.read_text(encoding="utf-8")
    if hashlib.sha256(FIXED_CONTRACT_JSON_PATH.read_bytes()).hexdigest() != FIXED_CONTRACT_JSON_SHA256:
        raise RunStateError("fixed-loop migration contract JSON SHA-256 drifted")
    if hashlib.sha256(NESTED_CONTRACT_JSON_PATH.read_bytes()).hexdigest() != NESTED_CONTRACT_JSON_SHA256:
        raise RunStateError("nested fixed-loop migration contract JSON SHA-256 drifted")
    pools = {
        FIXED_SH_FILE: sh_lines,
        FIXED_PY_FILE: fixed_lines,
        NESTED_PY_FILE: nested_lines,
        FIXED_CONTRACT_FILE: fixed_json.splitlines(),
        NESTED_CONTRACT_FILE: nested_json.splitlines(),
    }
    for row in rows:
        lines = pools[row.source_file]
        if not (1 <= row.line_start <= row.line_end <= len(lines)):
            raise RunStateError(f"fixed-loop density anchor range invalid: {row.obligation_id}")
        if "\n".join(lines[row.line_start - 1:row.line_end]) != row.quote:
            raise RunStateError(f"fixed-loop density anchor drifted: {row.obligation_id}")


def _accounting(family: str) -> dict[str, int]:
    if family == FAMILY_FIXED:
        return {
            "ledger_sites": FIXED_LEDGER_SITE_COUNT,
            "assert_sites": FIXED_ASSERT_SITE_COUNT,
            "transport_sites": FIXED_TRANSPORT_SITE_COUNT,
            "wrapper_facade_sites": 1,
            "parsed_sites": FIXED_SITE_COUNT,
            "contract_parents": FIXED_PARENT_COUNT,
            "auxiliary_gate_envelopes": FIXED_AUX_ENVELOPE_COUNT,
            "leaves": FIXED_LEAF_COUNT,
            "charged_rows": FIXED_CHARGED_ROW_COUNT,
            "roster_rows": FIXED_ROSTER_ROW_COUNT,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
        }
    return {
        "ledger_sites": NESTED_LEDGER_SITE_COUNT,
        "assert_sites": NESTED_ASSERT_SITE_COUNT,
        "transport_sites": NESTED_TRANSPORT_SITE_COUNT,
        "wrapper_facade_sites": 1,
        "parsed_sites": NESTED_SITE_COUNT,
        "contract_parents": NESTED_PARENT_COUNT,
        "auxiliary_gate_envelopes": NESTED_AUX_ENVELOPE_COUNT,
        "leaves": NESTED_LEAF_COUNT,
        "charged_rows": NESTED_CHARGED_ROW_COUNT,
        "roster_rows": NESTED_ROSTER_ROW_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
    }


def _face_delta(family: str) -> dict[str, Any]:
    if family == FAMILY_FIXED:
        return {
            "face_row_index": 18,
            "face_case_id": STORY_FIXED,
            "face_parsed": FIXED_FACE_PARSED,
            "face_obligations": FIXED_FACE_OBLIGATIONS,
            "face_parsed_minus_obligations": FIXED_FACE_GAP,
            "recalibrated_site_caliber": FIXED_SITE_COUNT,
            "recalibrated_charged_row_caliber": FIXED_CHARGED_ROW_COUNT,
            "identity": (
                f"{FIXED_CHARGED_ROW_COUNT} charged rows = {FIXED_PARENT_COUNT} migration-contract "
                f"parent envelopes + {FIXED_LEAF_COUNT} leaves (1 shell facade + "
                f"{FIXED_ASSERT_SITE_COUNT} python assert atoms); site caliber "
                f"{FIXED_SITE_COUNT} = {FIXED_LEAF_COUNT} leaves + {FIXED_TRANSPORT_SITE_COUNT} "
                "parent-internal transport checkpoints"),
            "residual_count_gap": RESIDUAL_COUNT_GAP,
        }
    return {
        "face_row_index": 19,
        "face_case_id": STORY_NESTED,
        "face_parsed": NESTED_FACE_PARSED,
        "face_obligations": NESTED_FACE_OBLIGATIONS,
        "face_parsed_minus_obligations": NESTED_FACE_GAP,
        "recalibrated_site_caliber": NESTED_SITE_COUNT,
        "recalibrated_charged_row_caliber": NESTED_CHARGED_ROW_COUNT,
        "identity": (
            f"{NESTED_CHARGED_ROW_COUNT} charged rows = {NESTED_PARENT_COUNT} migration-contract "
            f"parent envelopes + {NESTED_LEAF_COUNT} leaves (1 shell facade + "
            f"{NESTED_ASSERT_SITE_COUNT} python assert atoms); site caliber "
            f"{NESTED_SITE_COUNT} = {NESTED_LEAF_COUNT} leaves + {NESTED_TRANSPORT_SITE_COUNT} "
            "parent-internal transport checkpoints"),
        "residual_count_gap": RESIDUAL_COUNT_GAP,
    }


def _envelope_reuse(family: str) -> dict[str, Any]:
    contract = _family_contract(family)
    return {
        "command": contract.command,
        "case_id": contract.case_id,
        "source_case_id": contract.source_case_id,
        "suite": contract.suite,
        "source_suite": contract.source_suite,
        "local_executor": contract.local_executor,
        "local_status": contract.local_status,
        "canonical_status": contract.canonical_status,
        "preprod_initial_verdict": contract.preprod_initial_verdict,
        "preprod_gaps": list(contract.preprod_gaps),
        "cli_dispatch_registered": True,
        "case_command_registration_registered": True,
        "new_registration_command": False,
        "reuse_statement": (
            "the charged leaves ride the already-registered dual dynamic-family command; "
            "cli.py dispatch and case_command_registration.py stay byte-identical this wave"),
    }


def _executor_binding(family: str) -> dict[str, Any]:
    if family == FAMILY_FIXED:
        return {
            "case_id": "DUAL-FIXED-LOOP-01",
            "suite": "dual-fixed-loop-v1",
            "source_case_id": "LOCAL-FIXED-LOOP-01",
            "source_suite": "owned-local-fixed-loop-v1",
            "frozen_ledger_site_parity": True,
            "required_ledger_site_count": FIXED_LEDGER_SITE_COUNT,
            "required_assertion_count": FIXED_LEDGER_SITE_COUNT,
            "ledger_partition": {
                "assert_sites": FIXED_ASSERT_SITE_COUNT,
                "transport_checkpoints": FIXED_TRANSPORT_SITE_COUNT,
            },
            "frozen_completion_gates": {
                "case_status": "LOCAL_FIXED_LOOP_PASS",
                "canonical_status": "LOCAL_SUITE_PASS",
                "completed_requirement_steps": FIXED_PARENT_COUNT,
                "judge_outcome_sequence": list(FIXED_JUDGE_OUTCOMES),
                "shared_control_resets_issued": 0,
                "per_run_receipt_partitions": {"approved-arm": 4, "exhausted-arm": 4},
                "per_run_output_event_counts": [6, 6],
                "one_shot_session_status": "running",
                "chat_publication_count": 1,
                "exact_cleanup_deletions": {"groups": 2, "provider_bot": 1},
                "non_target_state_deleted": 0,
                "judge_admissions": 4,
                "judge_request_count": 0,
                "provider_receipt_limit": 8,
                "per_run_output_refs_unique": 4,
                "judge_receipt_partition_disjoint": True,
                "cross_run_leakage": 0,
            },
        }
    return {
        "case_id": "DUAL-NESTED-FIXED-LOOP-01",
        "suite": "dual-nested-fixed-loop-v1",
        "source_case_id": "LOCAL-NESTED-FIXED-LOOP-01",
        "source_suite": "owned-local-nested-fixed-loop-v1",
        "frozen_ledger_site_parity": True,
        "required_ledger_site_count": NESTED_LEDGER_SITE_COUNT,
        "required_assertion_count": NESTED_LEDGER_SITE_COUNT,
        "ledger_partition": {
            "assert_sites": NESTED_ASSERT_SITE_COUNT,
            "transport_checkpoints": NESTED_TRANSPORT_SITE_COUNT,
        },
        "frozen_completion_gates": {
            "case_status": "LOCAL_NESTED_FIXED_LOOP_PASS",
            "canonical_status": "LOCAL_SUITE_PASS",
            "requirement_steps": NESTED_PARENT_COUNT,
            "story_markers": {
                "nested-bot-story": True,
                "nested-human-story": True,
            },
            "observed_checks": 2,
            "redaction_gate": True,
            "zero_traffic": {
                "observed_webhook_requests": 0,
                "observed_event_webhook_requests": 0,
                "judge_started": False,
            },
            "nested_bot_graph_shape": {"loops": 2, "nodes": 17, "completed": 15, "skipped": 2},
            "nested_human_graph_shape": {"loops": 3, "nodes": 31, "completed": 21, "skipped": 10},
            "child_exit_code": 0,
            "mock_child_ready": True,
            "mock_child_exit_code": 0,
            "mock_child_redaction_gate": True,
            "required_assertion_count": NESTED_LEDGER_SITE_COUNT,
            "assertions_all_pass": NESTED_LEDGER_SITE_COUNT,
            "story_markers_observed": {"nested-bot-story": True, "nested-human-story": True},
            "observed_marker_checks": 2,
            "mock_child_control_planes": [
                "provider_requests", "event_webhook_requests", "friend_auth_sync_requests"],
            "resource_disposition": "STORY_SCOPED_TEMPORARY_DIRECTORY",
            "credential_memory": "NOT_CAPTURED",
        },
    }


def _wrapper_facade(family: str) -> dict[str, Any]:
    envelope = wrapper_gate_envelope(family)
    return {
        "envelope_id": envelope["obligation_id"],
        "envelope_source": FACE_WRAPPER_GATE,
        "charged": False,
        "facade_leaf": FIXED_WRAPPER_LEAF if family == FAMILY_FIXED else NESTED_WRAPPER_LEAF,
        "shell_story_span": [envelope["line_start"], envelope["line_end"]],
        "shell_facade_assert_line": envelope["facade_assert_line"],
        "shell_facade_assert_message": envelope["facade_assert_message"],
        "disclosure": (
            "face-derived auxiliary pseudo-envelope: it owns the shell facade leaf (exit code 0) "
            "and is disclosed as derived from the executor-migration face wrapper, never as a "
            "migration-contract obligation; it is excluded from the charged parent caliber"),
    }


def fixed_loop_density_plan() -> dict[str, Any]:
    rows = fixed_loop_obligations()
    return _story_plan(FAMILY_FIXED, STORY_FIXED, rows)


def nested_fixed_loop_density_plan() -> dict[str, Any]:
    rows = nested_fixed_loop_obligations()
    return _story_plan(FAMILY_NESTED, STORY_NESTED, rows)


def _story_plan(family: str, story: str, rows: tuple[Obligation, ...]) -> dict[str, Any]:
    from .obligations import (  # noqa: PLC0415
        family_checkpoint_catalog, inherited_base_anchor_annex, site_free_parents,
        transport_checkpoint_union)
    accounting = _accounting(family)
    parents = [row for row in rows if row.level == "PARENT"]
    leaves = [row for row in rows if row.level == "LEAF"]
    contract_parents = [row for row in parents if row.charged]
    auxiliary = [row for row in parents if not row.charged]
    if len(contract_parents) != (FIXED_PARENT_COUNT if family == FAMILY_FIXED else NESTED_PARENT_COUNT):
        raise RunStateError(f"{family} charged parent caliber changed")
    if len(auxiliary) != 1:
        raise RunStateError(f"{family} auxiliary gate envelope caliber changed")
    if len(leaves) != (FIXED_LEAF_COUNT if family == FAMILY_FIXED else NESTED_LEAF_COUNT):
        raise RunStateError(f"{family} leaf caliber changed")
    if accounting["charged_rows"] != len(contract_parents) + len(leaves):
        raise RunStateError(f"{family} charged row identity broke")
    plan = {
        "schema_version": SCHEMA_VERSION,
        "contract": DENSITY_CONTRACT,
        "family": family,
        "parent_story": story,
        "source_freeze": {
            "shell_file": FIXED_SH_FILE,
            "shell_sha256": FIXED_SH_SHA256,
            "python_file": FIXED_PY_FILE if family == FAMILY_FIXED else NESTED_PY_FILE,
            "python_sha256": FIXED_PY_SHA256 if family == FAMILY_FIXED else NESTED_PY_SHA256,
            "migration_contract_file": (FIXED_CONTRACT_FILE if family == FAMILY_FIXED
                                        else NESTED_CONTRACT_FILE),
            "migration_contract_sha256": (FIXED_CONTRACT_JSON_SHA256 if family == FAMILY_FIXED
                                          else NESTED_CONTRACT_JSON_SHA256),
            "source_revision": SOURCE_REVISION,
            "supporting_freezes": {
                "common.sh": COMMON_SH_SHA256,
                "http_provider_judge_mock.py": PROVIDER_JUDGE_MOCK_SHA256,
            },
        },
        "parsed_site_caliber": accounting["parsed_sites"],
        "charged_row_caliber": accounting["charged_rows"],
        "upstream_roster_verdict": "0 green / 1 parsed facade (face snapshot; recalibrated by this wave)",
        "upstream_color_basis": B_UPSTREAM_NOT_ROSTERED,
        "accounting": accounting,
        "face_delta": _face_delta(family),
        "envelope_reuse": _envelope_reuse(family),
        "executor_binding": _executor_binding(family),
        "wrapper_facade": _wrapper_facade(family),
        "projection_rule": (
            "migration-contract parent envelopes are reused verbatim (ids, expectations, and for "
            "the fixed family the method/path/actor/kind columns) from the frozen JSON contracts; "
            "every executor-ledger python site charges exactly once: assert sites become charged "
            "leaves with a comparator and a frozen expected value, transport sites become "
            "parent-internal checkpoints disclosed on their owning envelopes; the shell facade "
            "charges as one leaf owned by a disclosed face-derived auxiliary gate envelope; "
            "conditional branches are charged with honest dormancy/leg disclosures, never "
            "excluded; nothing is normalized to PASS"),
        "projection_exclusions": [],
        "projection_exclusion_note": (
            "no source line is excluded: all ledger sites charge (assert sites as leaves, "
            "transport sites as parent-internal checkpoints), the shell facade charges as the "
            "wrapper leaf, and narration or fixture lines outside the two shell story functions "
            "are not story assertion sites"),
        "auxiliary_envelope": auxiliary[0].as_dict(),
        "unlocated_source_probes": [],
        "unlocated_count": 0,
        "parsed_unlocated_count": 0,
        "source_read_only": True,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_references": [DRIVER_TOKEN_ENV],
        "credential_reference_basis": (
            "the shell facade sets the driver token through the environment variable name only; "
            "the python stories read it via the base Story; no credential value is ever stored, "
            "quoted or resolved by this package"),
        "named_registrations": {key: dict(value) for key, value in NAMED_REGISTRATIONS.items()
                                if _registration_applies_to_family(key, family)},
        "internal_checkpoint_catalog": family_checkpoint_catalog(family),
        "transport_checkpoint_union": list(transport_checkpoint_union(family)),
        "site_free_parents": list(site_free_parents(family)),
        "inherited_base_anchors": inherited_base_anchor_annex(family),
        "obligations": [row.as_dict() for row in parents + leaves],
    }
    return plan


def _registration_applies_to_family(registration_id: str, family: str) -> bool:
    if registration_id == FACE_UNDERCOUNT_ID or registration_id == DUAL_FACE_DIVERGENCE_ID:
        return True
    if family == FAMILY_FIXED:
        return registration_id == FIXED_PREPROD_GAP_ID
    return registration_id == NESTED_PREPROD_GAP_ID


def fixed_loop_reconciliation() -> dict[str, Any]:
    return _reconciliation(FAMILY_FIXED)


def nested_fixed_loop_reconciliation() -> dict[str, Any]:
    return _reconciliation(FAMILY_NESTED)


def _reconciliation(family: str) -> dict[str, Any]:
    accounting = _accounting(family)
    face = _face_delta(family)
    return {
        "family": family,
        "site_caliber": {
            "parsed_sites": accounting["parsed_sites"],
            "leaves": accounting["leaves"],
            "parent_internal_checkpoints": accounting["transport_sites"],
            "identity": (
                f"{accounting['parsed_sites']} = {accounting['leaves']} leaves + "
                f"{accounting['transport_sites']} parent-internal transport checkpoints"),
            "residual_count_gap": RESIDUAL_COUNT_GAP,
        },
        "row_caliber": {
            "charged_rows": accounting["charged_rows"],
            "contract_parents": accounting["contract_parents"],
            "auxiliary_gate_envelopes": accounting["auxiliary_gate_envelopes"],
            "identity": (
                f"{accounting['charged_rows']} = {accounting['contract_parents']} contract "
                f"parent envelopes + {accounting['leaves']} leaves"),
            "residual_count_gap": RESIDUAL_COUNT_GAP,
        },
        "executor_ledger_parity": {
            "ledger_sites": accounting["ledger_sites"],
            "assert_sites": accounting["assert_sites"],
            "transport_sites": accounting["transport_sites"],
            "wrapper_facade": 1,
            "parity": accounting["ledger_sites"] + 1 == accounting["parsed_sites"],
        },
        "face_delta": face,
        "honesty": (
            "site caliber and row caliber are both disclosed and never merged; the face parsed "
            "snapshot (1) is the shell facade only; transport checkpoints ride their owning "
            "parent envelopes as internal checkpoints and are never double-charged as rows"),
    }


def family_reconciliation() -> dict[str, Any]:
    fixed = fixed_loop_reconciliation()
    nested = nested_fixed_loop_reconciliation()
    return {
        "families": {FAMILY_FIXED: fixed, FAMILY_NESTED: nested},
        "family_totals": {
            "leaves": FAMILY_LEAF_COUNT,
            "contract_parents": FAMILY_PARENT_COUNT,
            "auxiliary_gate_envelopes": 2,
            "charged_rows": FAMILY_CHARGED_ROW_COUNT,
            "roster_rows": FAMILY_ROSTER_ROW_COUNT,
            "parsed_sites": FAMILY_SITE_COUNT,
            "executor_ledger_sites": FAMILY_LEDGER_SITE_COUNT,
            "parent_internal_checkpoints": FIXED_TRANSPORT_SITE_COUNT + NESTED_TRANSPORT_SITE_COUNT,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
        },
        "identity": (
            f"{FAMILY_CHARGED_ROW_COUNT} charged rows = {FAMILY_PARENT_COUNT} contract parents + "
            f"{FAMILY_LEAF_COUNT} leaves; {FAMILY_SITE_COUNT} parsed sites = "
            f"{FAMILY_LEAF_COUNT} leaves + {FIXED_TRANSPORT_SITE_COUNT + NESTED_TRANSPORT_SITE_COUNT} "
            f"checkpoints; executor ledger {FAMILY_LEDGER_SITE_COUNT} python sites + 2 shell "
            "facades; residual_count_gap=0 in every caliber"),
    }


def family_accounting() -> dict[str, int]:
    return {
        "leaves": FAMILY_LEAF_COUNT,
        "contract_parents": FAMILY_PARENT_COUNT,
        "charged_rows": FAMILY_CHARGED_ROW_COUNT,
        "roster_rows": FAMILY_ROSTER_ROW_COUNT,
        "parsed_sites": FAMILY_SITE_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
    }


__all__ = [
    "B_UPSTREAM_NOT_ROSTERED", "BLOCKED_AFTER_ASSERTION_FAILURE", "COMMON_SH_SHA256",
    "DENSITY_CONTRACT", "DRIVER_TOKEN_ENV", "DUAL_FACE_DIVERGENCE_ID",
    "FACE_UNDERCOUNT_ID", "FACE_WRAPPER_GATE", "FAMILY_FIXED", "FAMILY_LEDGER_SITE_COUNT",
    "FAMILY_LEAF_COUNT", "FAMILY_NESTED", "FAMILY_PARENT_COUNT", "FAMILY_ROSTER_ROW_COUNT",
    "FAMILY_SITE_COUNT", "FAMILY_TOKEN", "FIXED_ASSERT_SITE_COUNT", "FIXED_CHARGED_ROW_COUNT",
    "FIXED_CONTRACT_ENVELOPE", "FIXED_CONTRACT_FILE", "FIXED_CONTRACT_JSON_SHA256",
    "FIXED_COVERAGE_MAP_ROW_NOTE_PROPOSAL", "FIXED_FACE_GAP", "FIXED_FACE_OBLIGATIONS",
    "FIXED_FACE_PARSED", "FIXED_FIRST_EDITOR_OUTPUT", "FIXED_FIRST_HUMAN_INPUT",
    "FIXED_FINAL_OUTPUT", "FIXED_LEDGER_SITE_COUNT", "FIXED_LEAF_COUNT",
    "FIXED_PARENT_COUNT", "FIXED_PY_FILE", "FIXED_PY_SHA256", "FIXED_ROSTER_ROW_COUNT",
    "FIXED_ROW_NOTE_PROPOSAL", "FIXED_ROW_TOKEN", "FIXED_SECOND_EDITOR_OUTPUT",
    "FIXED_SECOND_HUMAN_INPUT", "FIXED_SH_ASSERT_LINE", "FIXED_SH_FILE", "FIXED_SH_SHA256",
    "FIXED_SH_STORY_SPAN", "FIXED_SITE_COUNT", "FIXED_TRANSPORT_SITE_COUNT",
    "FIXED_WRAPPER_LEAF", "FIXED_JUDGE_OUTCOMES", "FIXED_PREFIX", "FIXED_PREPROD_GAP_ID",
    "GATE_ID_FIXED", "GATE_ID_NESTED", "LOCAL_EXECUTION", "NESTED_ASSERT_SITE_COUNT",
    "NESTED_AUX_ENVELOPE_COUNT", "NESTED_BOT_JUDGE_OUTCOMES", "NESTED_BOT_SKIPPED_FRAMES",
    "NESTED_BOT_STEPS", "NESTED_CHARGED_ROW_COUNT", "NESTED_CONTRACT_ENVELOPE",
    "NESTED_CONTRACT_FILE", "NESTED_CONTRACT_JSON_SHA256", "NESTED_COVERAGE_MAP_ROW_NOTE_PROPOSAL",
    "NESTED_FACE_GAP", "NESTED_FACE_OBLIGATIONS", "NESTED_FACE_PARSED",
    "NESTED_HUMAN_JUDGE_OUTCOMES", "NESTED_LEDGER_SITE_COUNT", "NESTED_LEAF_COUNT",
    "NESTED_PARENT_COUNT", "NESTED_PREFIX", "NESTED_PREPROD_GAP_ID", "NESTED_PY_FILE",
    "NESTED_PY_SHA256", "NESTED_ROSTER_ROW_COUNT", "NESTED_ROW_NOTE_PROPOSAL",
    "NESTED_ROW_TOKEN", "NESTED_SH_ASSERT_LINE", "NESTED_SH_STORY_SPAN", "NESTED_SITE_COUNT",
    "NESTED_TRANSPORT_SITE_COUNT", "NESTED_WRAPPER_LEAF", "OBSERVATION_MISMATCH",
    "Obligation", "PREPROD_EXECUTION", "PREPROD_INJECTED_EXECUTION", "PROVIDER_JUDGE_MOCK_SHA256",
    "RESIDUAL_COUNT_GAP", "RunStateError", "SHARED_CONTROL_RESETS", "SCHEMA_VERSION",
    "SiteSpan", "SOURCE_REVISION", "STATUS_BLOCKED_INVENTORIED", "STATUS_FAIL",
    "STATUS_NOT_RUN", "STATUS_PASS", "STORY_FIXED", "STORY_NESTED",
    "SYMBOL_CREDENTIAL_DOWNLINK", "SYMBOL_DELIVERY_EARLY", "SYMBOL_EXECUTION_NODE_FIRST_INNER",
    "SYMBOL_GROUP_ECHO", "SYMBOL_HUMAN_OUTPUT_FIRST_INNER", "SYMBOL_ARTIFACT",
    "SYMBOL_NODE_SECOND", "SYMBOL_REF_FIRST", "SYMBOL_REF_SECOND", "SYMBOL_SESSION_ECHO",
    "UPSTREAM_NOT_ROSTERED", "build_fixed_parent_envelopes", "build_nested_parent_envelopes",
    "build_wrapper_gate_envelopes", "family_accounting", "family_parent_envelopes",
    "family_reconciliation", "fixed_loop_density_plan", "fixed_loop_obligations",
    "fixed_loop_reconciliation", "nested_fixed_loop_density_plan",
    "nested_fixed_loop_obligations", "nested_fixed_loop_reconciliation", "obligations",
    "story_lines", "site_records", "site_span_index", "wrapper_gate_envelope",
    "wrapper_sh_story_lines",
]
