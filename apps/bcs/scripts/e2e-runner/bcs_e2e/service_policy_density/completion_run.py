"""Wave-D completion execution and honest receipt projection.

This module is the additive execution layer of the landed
``SERVICE_POLICY_DENSITY_R1_PROJECTED_20261031`` first wave: it evaluates the
fifteen marker-completion leaves ``SERVICE-POLICY.F33``-``F47`` offline against
the admitted dual-environment profiles and never opens a socket, resolves a
credential, or contacts preprod.  It reuses the first wave's replay seam
(``ReplayTransport``/``Observation``/``owned_local_replay_transport``) because
every completion leaf binds one of the nineteen already-reviewed station
operations; no new station face is introduced.

Three honesty gates are structural:

* the SHA-256 freeze of both upstream story files and the anchor-validated
  builder run before any evaluation, and ``assert_honesty_ledger`` fail-closes
  the layer when the frozen roster, the marker-unit disposition table, the
  contested-unit dual charge, the named form-difference item, the P4 material
  gate or the two-item gap ledger drift apart;
* the contested 410 marker unit keeps both charges visible: the completion
  replay charges only the frozen station form (station 410
  ``service_policy_retired``, observed 410 on the owned-local Singlebox
  2026-10-30 and in the r9-transportfix-duo preprod product-http journal as a
  disclosed observation), while the poolab sim observation of 200 stays
  registered as the documented exception carried verbatim on the F40 receipt
  row - nothing is re-judged and no sim-form PASS exists;
* the preprod tier has no offline演算 posture: without an injected admitted
  live transport the receipt fail-closes to ``BLOCKED_INVENTORIED`` with the
  named gap ``SERVICE_POLICY_DENSITY_COMPLETION_PREPROD_TRANSPORT_GAP``, every
  leaf stays ``NOT_RUN``, and gap ledger GAP-SP-D-01 (transport) plus
  GAP-SP-D-02 (form difference) are embedded - a planned remote leg never
  becomes PASS offline.

``remote_validation`` stays ``NOT_RUN`` and ``grants_release_credit`` stays
false on every receipt this module can produce.  PASS criteria follow the
first wave: every leaf evaluates PASS, any failure or transport exception
fails that row and marks every later row ``NOT_RUN`` with
``BLOCKED_AFTER_ASSERTION_FAILURE``.  Envelope-absent leaves disclose leaked
field names as the observed evidence of their FAIL and ``None`` on PASS, so
the synthetic replay identifiers never reach the serialized receipt.
"""

from __future__ import annotations

import re
from typing import Any

from ..profile import fingerprint
from ..run_state import RunStateError
from .completion import (
    BASIS_DOCUMENTED_EXCEPTION,
    COMPLETION_GAP_ID,
    COMPLETION_LEAF_COUNT,
    COMPLETION_WAVE_TOKEN,
    CONTRACT,
    COVERAGE_MAP_MATERIAL_GAP,
    ENVELOPE_FIELDS,
    FLAT_WRAPPER_OBLIGATIONS,
    FORM_DIFFERENCE_DEFECT_ID,
    FORM_DIFFERENCE_ITEM_ID,
    FORM_DIFFERENCE_NAMED_ITEM,
    GAP_GROUPS,
    GAP_LEDGER,
    HTTP_STATUS,
    HTTP_STATUS_ENUM,
    LAYER,
    LEDGER_GAP,
    MARKER_CHARGED_CHECKS,
    MATERIAL_GATE,
    P4_GATE_ID,
    PARSED_OBLIGATION_COUNT,
    PARENT,
    POLICY_ENVELOPE_ABSENT,
    RESIDUAL_COUNT_GAP,
    R1_WAVE_TOKEN,
    SOURCE_FILE,
    STORY_PY_SHA256,
    STORY_SH_FILE,
    STORY_SH_SHA256,
    CompletionObligation,
    build_service_policy_density_completion_obligations,
    completion_plan,
    completion_reconciliation,
    marker_unit_disposition,
)
from .profile import validate_service_policy_density_profile
from .run import (
    STATUS_BLOCKED_INVENTORIED,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    Observation,
    ReplayTransport,
    owned_local_replay_transport,
)

SCHEMA_VERSION = 1
EXECUTOR_LOCAL = "owned-local-singlebox-replay-completion"
EXECUTOR_PREPROD = "preprod-bcs-direct-legacy-completion"

COMPLETION_GAP_KIND = "PREPROD_EXECUTION_NOT_RUN_WAVE_D_COMPLETION"
COMPLETION_GAP_DETAIL = (
    "preprod retired-service-policy marker-completion requires an admitted "
    "bcs-direct legacy-face live transport for the fifteen Wave-D leaves; the "
    "offline projector fail-closes and keeps every completion leaf NOT_RUN "
    "instead of charging unexecuted rows, extending the family transport "
    "posture of SERVICE_POLICY_DENSITY_PREPROD_TRANSPORT_GAP")
COMPLETION_GAP_RESOLUTION = (
    "inject the admitted live transport through the run seam in the mainline "
    "serial flow after the P4 material gate (gate " + P4_GATE_ID + ") is "
    "observed; preprod admission, the M11 sim/station form-difference "
    "disposition and remote release validation remain separate gates")

BLOCKED_AFTER_FAILURE = "BLOCKED_AFTER_ASSERTION_FAILURE"
PREPROD_EXECUTION_NOT_RUN = "PREPROD_EXECUTION_NOT_RUN"
ASSERTION_FAILED = "SERVICE_POLICY_DENSITY_COMPLETION_ASSERTION_FAILED"
CONTESTED_MARKER_UNIT = "i"
LEAF_PREFIX_RE = re.compile(r"SERVICE-POLICY\.F\d{2}")

STATUS_VOCABULARY = frozenset({STATUS_PASS, STATUS_FAIL, STATUS_NOT_RUN,
                               STATUS_BLOCKED_INVENTORIED})

GAP_GROUP_LEGACY_COUNT = 7
GAP_GROUP_RETIRED_COUNT = 1
GAP_GROUP_INVOCATION_COUNT = 3
GAP_GROUP_RUNNING_COUNT = 1
GAP_GROUP_CONVERSION_COUNT = 3
ENVELOPE_LEAF_IDS = ("SERVICE-POLICY.F33", "SERVICE-POLICY.F35",
                     "SERVICE-POLICY.F37", "SERVICE-POLICY.F39")
CONTESTED_LEAF_ID = "SERVICE-POLICY.F40"
GAP_LEDGER_KEYS = ("GAP-SP-D-01", "GAP-SP-D-02")


def marker_unit_by_leaf() -> dict[str, str]:
    """Map each completion leaf to the marker unit it discharges.

    Thirteen leaves map 1:1 to their completion marker units; the contested
    unit ``i`` maps to F40 with both charges kept visible in the disposition
    table.  The mapping is derived from the frozen disposition table, never
    re-declared, so the two tables cannot drift apart.
    """
    mapping: dict[str, str] = {}
    for unit in marker_unit_disposition():
        charged_by = unit["charged_by"]
        if charged_by is None:
            continue
        charges = [charged_by] if isinstance(charged_by, str) else list(charged_by)
        for charge in charges:
            for leaf in LEAF_PREFIX_RE.findall(str(charge)):
                if leaf in mapping and mapping[leaf] != unit["unit"]:
                    raise RunStateError(
                        "service policy density completion marker unit "
                        f"double-charged: {leaf}")
                mapping[leaf] = unit["unit"]
    return mapping


MARKER_UNIT_BY_LEAF = marker_unit_by_leaf()


def _evaluate(row: CompletionObligation, observation: Observation) -> tuple[bool, Any]:
    comparator = row.comparator
    if comparator == HTTP_STATUS:
        return observation.status == row.expected, observation.status
    if comparator == HTTP_STATUS_ENUM:
        return observation.status in row.expected, observation.status
    if comparator == POLICY_ENVELOPE_ABSENT:
        body = observation.body
        leaked = sorted(field for field in ENVELOPE_FIELDS
                        if isinstance(body, dict) and field in body)
        return not leaked, (leaked if leaked else None)
    raise RunStateError(
        f"service policy density completion leaf {row.obligation_id} carries an "
        f"unreviewed comparator {comparator}")


def _obligation_receipt(row: CompletionObligation, status: str, *,
                        observed: Any = None,
                        error_type: str | None = None) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "obligation_id": row.obligation_id,
        "layer": LAYER,
        "level": row.level,
        "parent_obligation": row.parent_obligation,
        "family": row.family,
        "gap_group": row.gap_group,
        "label": row.label,
        "marker_unit": MARKER_UNIT_BY_LEAF[row.obligation_id],
        "kind": row.kind,
        "status": status,
        "density": row.density,
        "basis": row.basis,
        "method": row.method,
        "path": row.path,
        "caller": row.caller,
        "operation": row.operation,
        "source": {
            "file": SOURCE_FILE,
            "line_start": row.line_start,
            "line_end": row.line_end,
            "quote": row.source_quote,
            "assert_line": row.assert_line,
            "assert_quote": row.assert_quote,
            "request_line": row.request_line,
            "request_quote": row.request_quote,
            "envelope_def": row.envelope_def,
            "located": row.located,
        },
        "field": row.field,
        "comparator": row.comparator,
        "expected": row.expected if not isinstance(row.expected, tuple)
        else list(row.expected),
        "comparator_text": row.comparator_text,
        "expectation": row.expectation,
    }
    if observed is not None:
        receipt["observed"] = observed
    if error_type is not None:
        receipt["error_type"] = error_type
    if row.basis == BASIS_DOCUMENTED_EXCEPTION:
        # The contested 410 unit: the named form-difference item rides the row
        # verbatim so the register-not-rejudge posture is inspectable from the
        # receipt alone.
        receipt["documented_exception"] = dict(FORM_DIFFERENCE_NAMED_ITEM)
        receipt["form_difference"] = FORM_DIFFERENCE_ITEM_ID
    return receipt


def _gap_block() -> dict[str, Any]:
    return {
        "id": COMPLETION_GAP_ID,
        "kind": COMPLETION_GAP_KIND,
        "detail": COMPLETION_GAP_DETAIL,
        "resolution": COMPLETION_GAP_RESOLUTION,
        "gap_ledger_refs": sorted(GAP_LEDGER),
        "registered_exception": FORM_DIFFERENCE_ITEM_ID,
        "material_gate": P4_GATE_ID,
        "contested_marker_unit": CONTESTED_MARKER_UNIT,
    }


def honesty_ledger() -> dict[str, Any]:
    """The Wave-D honesty posture block embedded in every completion receipt."""
    recon = completion_reconciliation()
    return {
        "layer": LAYER,
        "wave_token": COMPLETION_WAVE_TOKEN,
        "prior_wave_token": R1_WAVE_TOKEN,
        "contract": CONTRACT,
        "completion_leaves": COMPLETION_LEAF_COUNT,
        "ledger_gap": LEDGER_GAP,
        "residual_count_gap": recon["residual_count_gap"],
        "contested_marker_unit": CONTESTED_MARKER_UNIT,
        "contested_unit_statement": (
            "the contested 410 marker unit keeps its flat frozen-anchor charge "
            "(SERVICE-POLICY.retired-settings-gone, station 410) and its "
            "completion documented-exception recharge (SERVICE-POLICY.F40); "
            "both charges stay visible, neither is re-judged"),
        "station_form_charged_locally": (
            "the owned-local replay charges only the frozen station form "
            "(410 service_policy_retired); its PASS is station-form replay "
            "truth and grants zero sim or preprod credit"),
        "product_defects": product_defects_registry(),
        "registered_exception": dict(FORM_DIFFERENCE_NAMED_ITEM),
        "material_gate": dict(MATERIAL_GATE),
        "gap_ledger": gap_ledger_descriptor(),
        "remote_validation": STATUS_NOT_RUN,
        "grants_release_credit": False,
        "no_sim_form_pass": True,
        "no_silent_rejudgment": True,
    }


def product_defects_registry() -> dict[str, dict[str, Any]]:
    """The named product/form-difference defect entry (coverage-map M11)."""
    return {
        FORM_DIFFERENCE_DEFECT_ID: {
            "defect_id": FORM_DIFFERENCE_DEFECT_ID,
            "status": "PRODUCT_FORM_DIFFERENCE_ON_FILE",
            "item_id": FORM_DIFFERENCE_ITEM_ID,
            "coverage_map_material_gap": COVERAGE_MAP_MATERIAL_GAP,
            "registered_leaf": CONTESTED_LEAF_ID,
            "basis": BASIS_DOCUMENTED_EXCEPTION,
            "station_form_expected": 410,
            "sim_form_observed": 200,
            "registered_not_rejudged": True,
            "obligation_credit_claimed": (
                "station-form replay truth only; zero sim or preprod credit"),
        }
    }


def defect_gate_descriptor() -> dict[str, Any]:
    """Canonical defect-gate block for retention notes and proposals."""
    entry = product_defects_registry()[FORM_DIFFERENCE_DEFECT_ID]
    return {
        "defect_id": entry["defect_id"],
        "status": entry["status"],
        "coverage_map_material_gap": entry["coverage_map_material_gap"],
        "registered_leaf": entry["registered_leaf"],
        "basis": entry["basis"],
        "station_form": "410 service_policy_retired (frozen router.rs station form)",
        "sim_form": "200 (poolab roster recorded sim observation)",
        "registered_not_rejudged": True,
        "raw_row_evaluation": (
            "the completion replay evaluates the frozen station form only; the "
            "sim observation stays registered as exception evidence; a PASS on "
            "SERVICE-POLICY.F40 is station-form replay truth, never sim-form "
            "credit"),
        "closure_requirements": [
            "authorized preprod station observation under the P4 material gate",
            "M11 product-defect disposition by product",
        ],
    }


def gap_ledger_descriptor() -> dict[str, Any]:
    """Canonical two-entry gap ledger block embedded in every completion receipt."""
    return {
        gap_id: {
            "gap_id": entry["gap_id"],
            "scope": entry["scope"],
            "kind": entry["kind"],
            "status": entry["status"],
            "blocks": entry["blocks"],
            "parsed_unit_claimed": entry["parsed_unit_claimed"],
        }
        for gap_id, entry in GAP_LEDGER.items()
    }


def assert_honesty_ledger() -> None:
    """Fail closed when any frozen Wave-D structure drifts.

    The ledger runs on the frozen tables themselves (the builder re-verifies
    both source-file SHA-256 digests first), so a drift in any constant, roster
    row, disposition unit, named item, gate or gap entry fails the layer
    before a receipt can be produced.
    """
    leaves = build_service_policy_density_completion_obligations()
    leaf_ids = [row.obligation_id for row in leaves]
    expected_ids = [f"SERVICE-POLICY.F{index:02d}"
                    for index in range(33, 33 + COMPLETION_LEAF_COUNT)]
    if leaf_ids != expected_ids:
        raise RunStateError(
            "service policy density completion honesty ledger: leaf id sequence drifted")
    gap_counts: dict[str, int] = {}
    for row in leaves:
        gap_counts[row.gap_group] = gap_counts.get(row.gap_group, 0) + 1
    if tuple(sorted(gap_counts)) != tuple(sorted(GAP_GROUPS)):
        raise RunStateError(
            "service policy density completion honesty ledger: gap groups drifted")
    if (gap_counts.get("legacy-policy-absent-create-read") != GAP_GROUP_LEGACY_COUNT
            or gap_counts.get("retired-settings-gone-410") != GAP_GROUP_RETIRED_COUNT
            or gap_counts.get("invocation-not-blocked") != GAP_GROUP_INVOCATION_COUNT
            or gap_counts.get("running-conflict") != GAP_GROUP_RUNNING_COUNT
            or gap_counts.get("chat-completion-guard") != GAP_GROUP_CONVERSION_COUNT):
        raise RunStateError(
            "service policy density completion honesty ledger: gap group counts drifted")
    basis_counts: dict[str, int] = {}
    for row in leaves:
        basis_counts[row.basis] = basis_counts.get(row.basis, 0) + 1
    if basis_counts != {"frozen-anchor": 14, "documented-exception": 1}:
        raise RunStateError(
            "service policy density completion honesty ledger: basis counts drifted")
    envelope_leaves = [row.obligation_id for row in leaves if row.envelope_def]
    if list(envelope_leaves) != list(ENVELOPE_LEAF_IDS):
        raise RunStateError(
            "service policy density completion honesty ledger: envelope leaves drifted")
    if any(row.envelope_def != (row.comparator == POLICY_ENVELOPE_ABSENT)
           for row in leaves):
        raise RunStateError(
            "service policy density completion honesty ledger: envelope comparator pairing drifted")
    if any(row.comparator not in {HTTP_STATUS, HTTP_STATUS_ENUM,
                                  POLICY_ENVELOPE_ABSENT} for row in leaves):
        raise RunStateError(
            "service policy density completion honesty ledger: comparator vocabulary drifted")
    exceptions = [row for row in leaves if row.basis == BASIS_DOCUMENTED_EXCEPTION]
    if (len(exceptions) != 1 or exceptions[0].obligation_id != CONTESTED_LEAF_ID
            or exceptions[0].documented_exception["item_id"] != FORM_DIFFERENCE_ITEM_ID):
        raise RunStateError(
            "service policy density completion honesty ledger: documented-exception leaf drifted")
    recon = completion_reconciliation()
    if recon["residual_count_gap"] != RESIDUAL_COUNT_GAP or RESIDUAL_COUNT_GAP != 0:
        raise RunStateError(
            "service policy density completion honesty ledger: residual count gap opened")
    if MARKER_CHARGED_CHECKS - FLAT_WRAPPER_OBLIGATIONS - LEDGER_GAP != 0:
        raise RunStateError(
            "service policy density completion honesty ledger: ledger formula broken")
    dispositions = marker_unit_disposition()
    if len(dispositions) != MARKER_CHARGED_CHECKS:
        raise RunStateError(
            "service policy density completion honesty ledger: marker units drifted")
    unit_ids = [unit["unit"] for unit in dispositions]
    if sorted(unit_ids) != [chr(ord("a") + index) for index in range(23)]:
        raise RunStateError(
            "service policy density completion honesty ledger: marker unit ids drifted")
    by_unit = {unit["unit"]: unit for unit in dispositions}
    contested_text = str(by_unit[CONTESTED_MARKER_UNIT]["charged_by"])
    if ("SERVICE-POLICY.retired-settings-gone" not in contested_text
            or CONTESTED_LEAF_ID not in contested_text):
        raise RunStateError(
            "service policy density completion honesty ledger: contested unit charges not both visible")
    completion_ids = set(leaf_ids)
    for unit in dispositions:
        if unit["disposition"] == "completion_leaf":
            if unit["charged_by"] not in completion_ids:
                raise RunStateError(
                    "service policy density completion honesty ledger: "
                    f"completion unit {unit['unit']} charges unknown leaf")
    form_item = recon["form_difference"]
    if (form_item["item_id"] != FORM_DIFFERENCE_ITEM_ID
            or form_item["station_form"]["expected_status"] != 410
            or form_item["sim_form"]["observed_status"] != 200
            or form_item["registered_leaf"] != CONTESTED_LEAF_ID
            or form_item["basis"] != BASIS_DOCUMENTED_EXCEPTION
            or form_item["defect_registry"]["coverage_map_material_gap"]
            != COVERAGE_MAP_MATERIAL_GAP
            or "grants_release_credit" not in form_item["credit_posture"]):
        raise RunStateError(
            "service policy density completion honesty ledger: form-difference item drifted")
    requires_text = " ".join(MATERIAL_GATE["requires"])
    if (MATERIAL_GATE["gate_id"] != P4_GATE_ID
            or "BCS_E2E_HUMAN_COOKIE" not in requires_text
            or "BCS_E2E_DRIVER_TOKEN" not in requires_text
            or MATERIAL_GATE["disclosed_not_executed"] is not True):
        raise RunStateError(
            "service policy density completion honesty ledger: material gate drifted")
    ledger = recon["gap_ledger"]
    if set(ledger) != set(GAP_LEDGER_KEYS):
        raise RunStateError(
            "service policy density completion honesty ledger: gap ledger drifted")
    if (ledger["GAP-SP-D-01"]["kind"] != "EXECUTION_GAP_NOT_RUN"
            or ledger["GAP-SP-D-02"]["status"] != "REGISTERED_NOT_REJUDGED"
            or not ledger["GAP-SP-D-01"]["parsed_unit_claimed"]
            or not ledger["GAP-SP-D-02"]["parsed_unit_claimed"]):
        raise RunStateError(
            "service policy density completion honesty ledger: gap ledger entries drifted")
    if (STORY_PY_SHA256 != recon["sha256_freeze"][SOURCE_FILE]
            or STORY_SH_SHA256 != recon["sha256_freeze"][STORY_SH_FILE]):
        raise RunStateError(
            "service policy density completion honesty ledger: sha freeze disclosure drifted")
    if recon["family_density_rows"] != PARSED_OBLIGATION_COUNT + COMPLETION_LEAF_COUNT:
        raise RunStateError(
            "service policy density completion honesty ledger: family density rows drifted")


def density_accounting(receipt: dict[str, Any]) -> dict[str, Any]:
    """Derive the completion-layer accounting from a projection receipt."""
    rows = receipt["assertions"]
    counts = {"PASS": 0, "FAIL": 0, "NOT_RUN": 0}
    for row in rows:
        status = row["status"]
        if status in counts:
            counts[status] += 1
    contested = next((row for row in rows
                      if row["obligation_id"] == CONTESTED_LEAF_ID), None)
    return {
        "layer": LAYER,
        "counts": counts,
        "expected_total": COMPLETION_LEAF_COUNT,
        "observed_total": len(rows),
        "residual_count_gap": COMPLETION_LEAF_COUNT - len(rows),
        "case_status": receipt.get("status"),
        "executor": receipt.get("executor"),
        "transport_injected": receipt.get("execution", {}).get("transport_injected"),
        "contested_leaf": {
            "obligation_id": CONTESTED_LEAF_ID,
            "status": None if contested is None else contested["status"],
            "observed_status": None if contested is None else contested.get("observed"),
            "basis": None if contested is None else contested.get("basis"),
            "form_difference": FORM_DIFFERENCE_ITEM_ID,
        },
        "open_gaps": sorted(gap_ledger_descriptor()),
        "material_gate": P4_GATE_ID,
        "remote_validation": receipt.get("remote_validation"),
        "grants_release_credit": receipt.get("grants_release_credit"),
    }


def family_accounting() -> dict[str, Any]:
    """Frozen family accounting basis across all three density calibers."""
    recon = completion_reconciliation()
    return {
        "parent": PARENT,
        "flat_wrapper_obligations": FLAT_WRAPPER_OBLIGATIONS,
        "r1_density_superset_rows": PARSED_OBLIGATION_COUNT,
        "completion_leaves": COMPLETION_LEAF_COUNT,
        "family_density_rows": PARSED_OBLIGATION_COUNT + COMPLETION_LEAF_COUNT,
        "marker_charged_checks": MARKER_CHARGED_CHECKS,
        "ledger_gap": LEDGER_GAP,
        "residual_count_gap": recon["residual_count_gap"],
        "contested_marker_unit": CONTESTED_MARKER_UNIT,
        "basis_counts": {"frozen-anchor": 14, "documented-exception": 1},
        "layer": LAYER,
        "wave_token": COMPLETION_WAVE_TOKEN,
        "prior_wave_token": R1_WAVE_TOKEN,
    }


def md_table(receipt: dict[str, Any]) -> str:
    """Render the per-leaf completion table for retention notes."""
    lines = [
        "| leaf | marker unit | family | gap group | basis | status | observed | source |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in receipt["assertions"]:
        source = row["source"]
        observed = row.get("observed", "")
        if isinstance(observed, (dict, list)):
            observed = "<json>"
        exception = " documented-exception" if "form_difference" in row else ""
        lines.append(
            f"| {row['obligation_id']} | {row.get('marker_unit', '')} | "
            f"{row['family']}{exception} | {row['gap_group']} | {row['basis']} | "
            f"{row['status']} | {observed} | "
            f"{source['file']}:{source['line_start']}-{source['line_end']} |")
    return "\n".join(lines)


def _environment_block(profile: dict[str, Any]) -> dict[str, Any]:
    environment = profile["environment"]
    entrypoint = profile["entrypoint"]
    return {
        "tier": environment["tier"],
        "id": environment["id"],
        "entrypoint_kind": entrypoint["kind"],
        "api_face": entrypoint["api_face"],
    }


def _case_scope(leaves: tuple[CompletionObligation, ...]) -> dict[str, Any]:
    return {
        "layer": LAYER,
        "gap_groups": [
            {"gap_group": group, "leaf_count": sum(
                1 for row in leaves if row.gap_group == group)}
            for group in (
                "legacy-policy-absent-create-read", "retired-settings-gone-410",
                "invocation-not-blocked", "running-conflict",
                "chat-completion-guard")],
        "completion_leaves": COMPLETION_LEAF_COUNT,
        "ledger_gap": LEDGER_GAP,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "marker_charged_checks": MARKER_CHARGED_CHECKS,
        "flat_wrapper_obligations": FLAT_WRAPPER_OBLIGATIONS,
        "family_density_rows": PARSED_OBLIGATION_COUNT + COMPLETION_LEAF_COUNT,
        "contested_marker_unit": CONTESTED_MARKER_UNIT,
        "envelope_absent_leaves": [
            row.obligation_id for row in leaves if row.envelope_def],
        "documented_exception_leaves": [
            row.obligation_id for row in leaves
            if row.basis == BASIS_DOCUMENTED_EXCEPTION],
        "marker_units": dict(MARKER_UNIT_BY_LEAF),
    }


def run_service_policy_density_completion(
        *, profile: dict[str, Any],
        transport: ReplayTransport | None = None) -> dict[str, Any]:
    """Project the fifteen Wave-D completion leaves offline.

    ``transport`` defaults to the deterministic owned-local replay fixture on
    the local tier (offline演算 of the frozen Singlebox story envelope reused
    from the first wave).  The preprod tier fail-closes offline to
    ``BLOCKED_INVENTORIED`` with the named gap, the registered form difference,
    the P4 material gate and the two-entry gap ledger; supplying an injected
    transport (the future live-admission seam) evaluates it with identical
    PASS criteria on both tiers.
    """
    profile = validate_service_policy_density_profile(profile)
    assert_honesty_ledger()
    leaves = build_service_policy_density_completion_obligations()
    plan = completion_plan()
    scope = _case_scope(leaves)
    tier = profile["environment"]["tier"]
    environment = _environment_block(profile)
    executor = (EXECUTOR_LOCAL if tier == "local" else EXECUTOR_PREPROD)

    if transport is None and tier == "preprod":
        assertions = [_obligation_receipt(row, STATUS_NOT_RUN,
                                          error_type=PREPROD_EXECUTION_NOT_RUN)
                      for row in leaves]
        return {
            "schema_version": SCHEMA_VERSION,
            "contract": CONTRACT,
            "layer": LAYER,
            "case_id": plan["case_id"],
            "parent": plan["parent"],
            "status": STATUS_BLOCKED_INVENTORIED,
            "environment": environment,
            "executor": executor,
            "profile_digest": fingerprint(profile),
            "case_scope": scope,
            "wrapper_command": plan["wrapper_command"],
            "new_registration_command": plan["new_registration_command"],
            "assertions": assertions,
            "execution": {
                "status": STATUS_NOT_RUN,
                "transport_injected": False,
                "completed_count": 0,
                "failed_count": 0,
                "total_count": COMPLETION_LEAF_COUNT,
                "error_type": PREPROD_EXECUTION_NOT_RUN,
            },
            "reports": {
                "expected_obligation_count": COMPLETION_LEAF_COUNT,
                "observed_obligation_count": len(assertions),
                "executed_obligation_count": 0,
                "pass_count": 0,
            },
            "gap": _gap_block(),
            "honesty": honesty_ledger(),
            "product_defects": product_defects_registry(),
            "registered_exception": dict(FORM_DIFFERENCE_NAMED_ITEM),
            "material_gate": dict(MATERIAL_GATE),
            "gap_ledger": gap_ledger_descriptor(),
            "reconciliation": completion_reconciliation(),
            "remote_validation": STATUS_NOT_RUN,
            "grants_release_credit": False,
        }

    if transport is None:
        transport = owned_local_replay_transport(profile)

    assertions: list[dict[str, Any]] = []
    failed = False
    for row in leaves:
        if failed:
            assertions.append(_obligation_receipt(
                row, STATUS_NOT_RUN, error_type=BLOCKED_AFTER_FAILURE))
            continue
        try:
            observation = transport.request(row.operation, row.obligation_id)
            passed, observed = _evaluate(row, observation)
            assertions.append(_obligation_receipt(
                row, STATUS_PASS if passed else STATUS_FAIL, observed=observed))
            if not passed:
                failed = True
        except Exception as error:  # transport failures are evidence, not PASS
            assertions.append(_obligation_receipt(
                row, STATUS_FAIL, error_type=type(error).__name__))
            failed = True
    status = STATUS_PASS if not failed else STATUS_FAIL
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": CONTRACT,
        "layer": LAYER,
        "case_id": plan["case_id"],
        "parent": plan["parent"],
        "status": status,
        "environment": environment,
        "executor": executor,
        "profile_digest": fingerprint(profile),
        "case_scope": scope,
        "wrapper_command": plan["wrapper_command"],
        "new_registration_command": plan["new_registration_command"],
        "assertions": assertions,
        "execution": {
            "status": status,
            "transport_injected": True,
            "completed_count": sum(
                item["status"] == STATUS_PASS for item in assertions),
            "failed_count": sum(
                item["status"] == STATUS_FAIL for item in assertions),
            "total_count": COMPLETION_LEAF_COUNT,
            "error_type": None if status == STATUS_PASS else ASSERTION_FAILED,
        },
        "reports": {
            "expected_obligation_count": COMPLETION_LEAF_COUNT,
            "observed_obligation_count": len(assertions),
            "executed_obligation_count": sum(
                item["status"] != STATUS_NOT_RUN for item in assertions),
            "pass_count": sum(item["status"] == STATUS_PASS for item in assertions),
        },
        "honesty": honesty_ledger(),
        "product_defects": product_defects_registry(),
        "registered_exception": dict(FORM_DIFFERENCE_NAMED_ITEM),
        "material_gate": dict(MATERIAL_GATE),
        "gap_ledger": gap_ledger_descriptor(),
        "reconciliation": completion_reconciliation(),
        "remote_validation": STATUS_NOT_RUN,
        "grants_release_credit": False,
    }
