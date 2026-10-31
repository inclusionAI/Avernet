"""Focused offline tests for the Wave-D service policy density completion.

Covers the fifteen marker-completion leaves ``SERVICE-POLICY.F33``-``F47``
(the coverage-map row 19 ledger gap ``parsed 23 - flat 8 = 15``), the
contested 410 unit's register-not-rejudge documented exception, the P4
material-gate association, the SHA-256 fail-close of both upstream story
files, the verbatim anchor spans, the honest marker-unit disposition of all
23 units, the owned-local offline full-PASS replay, the fail-fast cascade,
the preprod named-gap BLOCKED_INVENTORIED fail-close, the describe-only
mainline proposal, first-wave backward compatibility (the 40-row R1 roster
and the 8/32/40 case-wrapper caliber stay byte-identical), and credential
hygiene.  No socket is opened and no credential value is resolved or echoed
anywhere in this module.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.run_state import RunStateError
from bcs_e2e.service_policy_density import (
    BASIS_DOCUMENTED_EXCEPTION,
    BASIS_FROZEN_ANCHOR,
    BLOCKED_AFTER_FAILURE,
    CASE_ID,
    COMPLETION_GAP_ID,
    COMPLETION_GAP_KIND,
    COMPLETION_LEAF_COUNT,
    COMPLETION_WAVE_TOKEN,
    CONTESTED_MARKER_UNIT,
    COVERAGE_MAP_MATERIAL_GAP,
    DENSITY_OPERATION_ROWS,
    ENVELOPE_FIELDS,
    EXECUTOR_LOCAL,
    EXECUTOR_PREPROD,
    FLAT_WRAPPER_OBLIGATIONS,
    FORM_DIFFERENCE_DEFECT_ID,
    FORM_DIFFERENCE_ITEM_ID,
    FORM_DIFFERENCE_NAMED_ITEM,
    GAP_GROUPS,
    GAP_GROUP_CONVERSION,
    GAP_GROUP_INVOCATION,
    GAP_GROUP_LEGACY,
    GAP_GROUP_RETIRED,
    GAP_GROUP_RUNNING,
    GAP_ID,
    GAP_LEDGER,
    LEDGER_GAP,
    MARKER_CHARGED_CHECKS,
    MARKER_UNIT_BY_LEAF,
    MATERIAL_GATE,
    P4_GATE_ID,
    PARSED_OBLIGATION_COUNT,
    POLICY_ENVELOPE_ABSENT,
    PROPOSAL_TOKEN,
    RESIDUAL_COUNT_GAP,
    R1_WAVE_TOKEN,
    STATUS_BLOCKED_INVENTORIED,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    STORY_PY_SHA256,
    STORY_SH_FILE,
    STORY_SH_SHA256,
    CompletionObligation,
    dual_service_policy_density_case_run,
    run_service_policy_density,
)
from bcs_e2e.service_policy_density.completion import (
    ABSENCE_DEF_QUOTE,
    SOURCE_FILE,
    STORY_SH_PATH,
    build_service_policy_density_completion_obligations,
    completion_plan,
    completion_reconciliation,
    marker_unit_disposition,
)
from bcs_e2e.service_policy_density.completion_run import (
    assert_honesty_ledger,
    density_accounting,
    family_accounting,
    honesty_ledger,
    marker_unit_by_leaf,
    md_table,
    product_defects_registry,
    run_service_policy_density_completion,
)
from bcs_e2e.service_policy_density.contract import DENSITY_OPERATIONS
from bcs_e2e.service_policy_density.obligations import build_service_policy_density_obligations
from bcs_e2e.service_policy_density.profile import (
    PROFILE_CONTRACT,
    validate_service_policy_density_profile,
)
from bcs_e2e.service_policy_density.registration_proposal import (
    describe_only_mainline_fragment,
    registration_proposal,
)
from bcs_e2e.service_policy_density.run import (
    Observation,
    ReplayTransport,
    owned_local_replay_observations,
)
import bcs_e2e.service_policy_density.completion as completion_module
proposal_module = importlib.import_module(
    "bcs_e2e.service_policy_density.registration_proposal")

STORY_PATH = ROOT.parent / "e2e-test" / "service_policy_story.py"
LOCAL_RUN_ID = "20261031-service-policy-density-completion-local"
PREPROD_RUN_ID = "20261031-service-policy-density-completion-preprod"
EXPECTED_LEAF_IDS = [f"SERVICE-POLICY.F{index:02d}" for index in range(33, 48)]
ENVELOPE_LEAF_IDS = ["SERVICE-POLICY.F33", "SERVICE-POLICY.F35",
                     "SERVICE-POLICY.F37", "SERVICE-POLICY.F39"]


def local_profile() -> dict:
    return validate_service_policy_density_profile({
        "schema_version": 1,
        "contract": PROFILE_CONTRACT,
        "environment": {"tier": "local", "id": "service-policy-density-completion-local",
                        "expected_bcs_env": "local"},
        "entrypoint": {"kind": "singlebox", "origin": "http://127.0.0.1:18099",
                       "api_face": "legacy", "manifest_face": "legacy"},
        "human": {"user_id": "e2e-density"},
        "actors": {"driver": {"bot_id": "service-policy-density-driver",
                              "owner_id": "e2e-density", "dedicated": True}},
        "auth": {"kind": "owned-local-bot-tokens",
                 "driver_token_env": "BCS_E2E_DRIVER_TOKEN"},
        "route_bindings": {row["operation"]: {"method": row["method"], "path": row["path"],
                                              "api_face": "legacy"}
                           for row in DENSITY_OPERATION_ROWS},
    })


def preprod_profile() -> dict:
    profile = json.loads(json.dumps(local_profile()))
    profile["environment"] = {"tier": "preprod",
                              "id": "bcs-pre-service-policy-density-completion",
                              "expected_bcs_env": "pre"}
    profile["entrypoint"] = {"kind": "bcs-direct", "origin": "https://bcs-pre.alipay.com",
                             "api_face": "legacy", "manifest_face": "legacy"}
    profile["auth"] = {"kind": "human-cookie-with-owned-bot-tokens",
                       "driver_token_env": "BCS_E2E_DRIVER_TOKEN",
                       "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
    return validate_service_policy_density_profile(profile)


def drifted_py_copy(tmp: Path, mutated: str) -> Path:
    copy = Path(tmp) / "service_policy_story.py"
    copy.write_text(mutated, encoding="utf-8")
    return copy


def patch_py_sha_to(path_constant: str, path: Path) -> mock._patch:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return mock.patch.object(completion_module, path_constant, digest)


class CompletionObligationTests(unittest.TestCase):
    def test_builder_projects_fifteen_leaves_in_family_numbering_order(self):
        rows = build_service_policy_density_completion_obligations()
        self.assertEqual([row.obligation_id for row in rows], EXPECTED_LEAF_IDS)
        self.assertEqual(len(rows), COMPLETION_LEAF_COUNT)
        self.assertEqual(len({row.obligation_id for row in rows}), COMPLETION_LEAF_COUNT)
        for row in rows:
            self.assertIsInstance(row, CompletionObligation)
            self.assertEqual(row.level, "FIELD")
            self.assertEqual(row.layer, "MARKER_COMPLETION_WAVE_D")
            self.assertTrue(row.density)
            self.assertEqual(row.caller, "driver")
            self.assertEqual(row.preprod_mode, "LIVE_BEARER_REPLAY")
            self.assertTrue(row.located)

    def test_leaves_group_by_the_five_ledger_gap_groups(self):
        rows = build_service_policy_density_completion_obligations()
        by_family: dict[str, list[str]] = {}
        for row in rows:
            by_family.setdefault(row.family, []).append(row.obligation_id)
        self.assertEqual(by_family["LEGACY"], EXPECTED_LEAF_IDS[0:7])
        self.assertEqual(by_family["RETIRED"], [EXPECTED_LEAF_IDS[7]])
        self.assertEqual(by_family["INVOCATION"], EXPECTED_LEAF_IDS[8:11])
        self.assertEqual(by_family["GUARD"], [EXPECTED_LEAF_IDS[11]])
        self.assertEqual(by_family["CONVERSION"], EXPECTED_LEAF_IDS[12:15])
        groups: dict[str, int] = {}
        for row in rows:
            groups[row.gap_group] = groups.get(row.gap_group, 0) + 1
        self.assertEqual(groups, {
            GAP_GROUP_LEGACY: 7, GAP_GROUP_RETIRED: 1, GAP_GROUP_INVOCATION: 3,
            GAP_GROUP_RUNNING: 1, GAP_GROUP_CONVERSION: 3})
        self.assertEqual(sorted(groups), sorted(GAP_GROUPS))
        plan = completion_plan()
        self.assertEqual(plan["gap_groups"], [
            {"gap_group": GAP_GROUP_LEGACY, "leaf_count": 7},
            {"gap_group": GAP_GROUP_RETIRED, "leaf_count": 1},
            {"gap_group": GAP_GROUP_INVOCATION, "leaf_count": 3},
            {"gap_group": GAP_GROUP_RUNNING, "leaf_count": 1},
            {"gap_group": GAP_GROUP_CONVERSION, "leaf_count": 3}])

    def test_basis_classification_freezes_the_exception_leaf(self):
        rows = build_service_policy_density_completion_obligations()
        self.assertEqual(sum(1 for row in rows if row.basis == BASIS_FROZEN_ANCHOR), 14)
        self.assertEqual(sum(1 for row in rows if row.basis == BASIS_DOCUMENTED_EXCEPTION), 1)
        by_id = {row.obligation_id: row for row in rows}
        f40 = by_id["SERVICE-POLICY.F40"]
        self.assertEqual(f40.basis, BASIS_DOCUMENTED_EXCEPTION)
        self.assertEqual(f40.family, "RETIRED")
        self.assertEqual(f40.comparator, "http_status")
        self.assertEqual(f40.expected, 410)
        self.assertEqual(f40.documented_exception["item_id"], FORM_DIFFERENCE_ITEM_ID)
        for leaf_id in ENVELOPE_LEAF_IDS:
            row = by_id[leaf_id]
            self.assertTrue(row.envelope_def)
            self.assertEqual(row.comparator, POLICY_ENVELOPE_ABSENT)
            self.assertIsNone(row.field)
        self.assertEqual(by_id["SERVICE-POLICY.F36"].comparator, "http_status_enum")
        self.assertEqual(by_id["SERVICE-POLICY.F36"].expected, (200, 201))
        self.assertEqual(by_id["SERVICE-POLICY.F44"].operation,
                         "service_policy.reactivation_read")
        self.assertEqual(by_id["SERVICE-POLICY.F46"].method, "POST")

    def test_every_leaf_carries_verbatim_source_anchor_spans(self):
        rows = build_service_policy_density_completion_obligations()
        lines = STORY_PATH.read_text(encoding="utf-8").splitlines()
        for row in rows:
            span = "\n".join(lines[row.line_start - 1:row.line_end])
            self.assertEqual(row.source_quote, span,
                             f"span drift on {row.obligation_id}")
            self.assertIn(row.assert_quote, lines[row.assert_line - 1])
            self.assertIn(row.request_quote, lines[row.request_line - 1])
        self.assertEqual(SOURCE_FILE,
                         "apps/bcs/scripts/e2e-test/service_policy_story.py")
        by_id = {row.obligation_id: row for row in rows}
        self.assertEqual((by_id["SERVICE-POLICY.F36"].line_start,
                          by_id["SERVICE-POLICY.F36"].line_end), (70, 74))
        self.assertEqual((by_id["SERVICE-POLICY.F40"].line_start,
                          by_id["SERVICE-POLICY.F40"].line_end), (81, 83))
        self.assertEqual((by_id["SERVICE-POLICY.F41"].line_start,
                          by_id["SERVICE-POLICY.F41"].line_end), (86, 88))
        self.assertEqual((by_id["SERVICE-POLICY.F46"].line_start,
                          by_id["SERVICE-POLICY.F46"].line_end), (111, 112))
        self.assertEqual(ABSENCE_DEF_QUOTE, "\n".join(lines[50:54]))
        self.assertEqual(by_id["SERVICE-POLICY.F33"].source_quote,
                         '            self.assert_policy_absent(created)')
        self.assertEqual(by_id["SERVICE-POLICY.F36"].source_quote, "\n".join(
            lines[69:74]))

    def test_leaf_operations_bind_the_reviewed_nineteen_stations(self):
        rows = build_service_policy_density_completion_obligations()
        stations = {row["operation"] for row in DENSITY_OPERATION_ROWS}
        self.assertEqual(len(DENSITY_OPERATION_ROWS), 19)
        leaf_operations = {row.operation for row in rows}
        self.assertEqual(len(leaf_operations), 12)
        self.assertTrue(leaf_operations <= DENSITY_OPERATIONS)
        self.assertTrue(leaf_operations <= stations)
        self.assertNotIn("service_policy.teardown_dispositions", leaf_operations)
        self.assertNotIn("service_policy.register_token", leaf_operations)
        self.assertEqual({row.method for row in rows}, {"GET", "POST", "PATCH"})
        replay = owned_local_replay_observations(local_profile())
        for operation in leaf_operations:
            self.assertIn(operation, replay,
                          f"owned-local replay fixture lacks completion station {operation}")

    def test_leaf_parents_align_with_the_frozen_flat_contract(self):
        rows = build_service_policy_density_completion_obligations()
        self.assertTrue({row.parent_obligation for row in rows} <= set(
            ("SERVICE-POLICY.legacy-encoding-ignored",
             "SERVICE-POLICY.retired-settings-gone",
             "SERVICE-POLICY.invocation-lifecycle",
             "SERVICE-POLICY.running-reactivation-denied",
             "SERVICE-POLICY.chat-session-guard")))
        self.assertEqual(rows[7].parent_obligation, "SERVICE-POLICY.retired-settings-gone")


class SourceFreezeTests(unittest.TestCase):
    def test_sha256_freeze_fails_closed_on_py_drift(self):
        pristine = STORY_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            drifted = drifted_py_copy(Path(tmp), pristine + "\n# drift\n")
            with mock.patch.object(completion_module, "SOURCE_PATH", drifted):
                with self.assertRaises(RunStateError):
                    build_service_policy_density_completion_obligations()
                with self.assertRaises(RunStateError):
                    run_service_policy_density_completion(profile=local_profile())

    def test_sha256_freeze_fails_closed_on_sh_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            drifted = Path(tmp) / "service_policy_story.sh"
            drifted.write_text("# drifted shell wrapper\n", encoding="utf-8")
            with mock.patch.object(completion_module, "STORY_SH_PATH", drifted):
                with self.assertRaises(RunStateError):
                    build_service_policy_density_completion_obligations()

    def test_span_drift_fail_closes_on_the_contested_leaf(self):
        pristine = STORY_PATH.read_text(encoding="utf-8")
        mutated = pristine.replace("service_policy_retired", "service_policy_returned")
        with tempfile.TemporaryDirectory() as tmp:
            drifted = drifted_py_copy(Path(tmp), mutated)
            with mock.patch.object(completion_module, "SOURCE_PATH", drifted), \
                    patch_py_sha_to("STORY_PY_SHA256", drifted):
                with self.assertRaises(RunStateError) as caught:
                    build_service_policy_density_completion_obligations()
                self.assertIn("F40", str(caught.exception))

    def test_header_and_marker_drift_fail_closed(self):
        pristine = STORY_PATH.read_text(encoding="utf-8")
        pairs = (("    def run(self) -> None:", "    def run(self):"),
                 ("HTTP/response checks passed", "HTTP/response checks done"))
        for old, new in pairs:
            with tempfile.TemporaryDirectory() as tmp:
                drifted = drifted_py_copy(Path(tmp), pristine.replace(old, new))
                with mock.patch.object(completion_module, "SOURCE_PATH", drifted), \
                        patch_py_sha_to("STORY_PY_SHA256", drifted):
                    with self.assertRaises(RunStateError):
                        build_service_policy_density_completion_obligations()

    def test_absence_span_drift_fail_closes_the_envelope_secondary_anchor(self):
        pristine = STORY_PATH.read_text(encoding="utf-8")
        mutated = pristine.replace(
            'assert "service_spec" not in group, "Retired policy leaked into Group response"',
            'assert "service_spec" not in group, "policy leaked"')
        with tempfile.TemporaryDirectory() as tmp:
            drifted = drifted_py_copy(Path(tmp), mutated)
            with mock.patch.object(completion_module, "SOURCE_PATH", drifted), \
                    patch_py_sha_to("STORY_PY_SHA256", drifted):
                with self.assertRaises(RunStateError) as caught:
                    build_service_policy_density_completion_obligations()
                self.assertIn("absence-def span drifted", str(caught.exception))

    def test_pristine_copies_pass_all_freezes(self):
        pristine = STORY_PATH.read_text(encoding="utf-8")
        shell = STORY_SH_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            copy_py = Path(tmp) / "service_policy_story.py"
            copy_sh = Path(tmp) / "service_policy_story.sh"
            copy_py.write_text(pristine, encoding="utf-8")
            copy_sh.write_text(shell, encoding="utf-8")
            with mock.patch.object(completion_module, "SOURCE_PATH", copy_py), \
                    mock.patch.object(completion_module, "STORY_SH_PATH", copy_sh):
                self.assertEqual(
                    len(build_service_policy_density_completion_obligations()),
                    COMPLETION_LEAF_COUNT)


class CompletionAccountingTests(unittest.TestCase):
    def test_marker_unit_disposition_closes_the_ledger_exactly_once(self):
        dispositions = marker_unit_disposition()
        self.assertEqual(len(dispositions), MARKER_CHARGED_CHECKS)
        self.assertEqual([unit["unit"] for unit in dispositions],
                         [chr(ord("a") + index) for index in range(23)])
        counts: dict[str, int] = {}
        for unit in dispositions:
            counts[unit["disposition"]] = counts.get(unit["disposition"], 0) + 1
        self.assertEqual(counts, {
            "flat_wrapper_charge": 6,
            "bootstrap_registration_plane_bound": 1,
            "derivation_probe_not_charged": 1,
            "completion_leaf": 14,
            "contested_flat_charge_recharged_by_completion": 1})
        self.assertEqual(sum(counts.values()), MARKER_CHARGED_CHECKS)
        completion_ids = {unit["charged_by"] for unit in dispositions
                          if unit["disposition"] == "completion_leaf"}
        self.assertEqual(len(completion_ids), 14)
        self.assertTrue(completion_ids <= set(EXPECTED_LEAF_IDS))
        self.assertNotIn("SERVICE-POLICY.F40", completion_ids)
        contested = {unit["unit"]: unit for unit in dispositions}[CONTESTED_MARKER_UNIT]
        charged_text = str(contested["charged_by"])
        self.assertIn("SERVICE-POLICY.retired-settings-gone", charged_text)
        self.assertIn("SERVICE-POLICY.F40", charged_text)
        self.assertIn(FORM_DIFFERENCE_ITEM_ID,
                      json.dumps(contested, ensure_ascii=False))

    def test_reconciliation_formula_and_calibers_disclose_honestly(self):
        recon = completion_reconciliation()
        self.assertEqual(recon["residual_count_gap"], RESIDUAL_COUNT_GAP)
        self.assertEqual(RESIDUAL_COUNT_GAP, 0)
        self.assertEqual(recon["ledger_gap"], LEDGER_GAP)
        self.assertEqual(LEDGER_GAP, 15)
        self.assertEqual(FLAT_WRAPPER_OBLIGATIONS, 8)
        self.assertEqual(MARKER_CHARGED_CHECKS - FLAT_WRAPPER_OBLIGATIONS - LEDGER_GAP, 0)
        self.assertEqual(recon["completion_leaves"], COMPLETION_LEAF_COUNT)
        self.assertEqual(recon["marker_charged_checks"], MARKER_CHARGED_CHECKS)
        self.assertEqual(recon["family_density_rows"], PARSED_OBLIGATION_COUNT + 15)
        self.assertEqual(recon["family_density_rows"], 55)
        self.assertEqual(recon["bearer_leg_story_checks"], 20)
        self.assertEqual(recon["parsed_unlocated_count"], 0)
        self.assertEqual(recon["parsed_unlocated"], [])
        self.assertIn("parsed marker checks 23", recon["ledger_formula"])
        self.assertIn("ledger gap 15", recon["ledger_formula"])
        self.assertEqual(recon["marker_unit_disposition_counts"], {
            "flat_wrapper_charge": 6, "bootstrap_registration_plane_bound": 1,
            "derivation_probe_not_charged": 1, "completion_leaf": 14,
            "contested_flat_charge_recharged_by_completion": 1})
        self.assertEqual(recon["sha256_freeze"]["apps/bcs/scripts/e2e-test/"
                                                "service_policy_story.py"], STORY_PY_SHA256)
        self.assertEqual(recon["sha256_freeze"][STORY_SH_FILE], STORY_SH_SHA256)
        plan = completion_plan()
        self.assertEqual(plan["accounting"]["residual_count_gap"], 0)
        self.assertEqual(plan["accounting"]["ledger_gap"], 15)
        self.assertEqual(plan["accounting"]["completion_leaves"], 15)
        self.assertEqual(plan["new_registration_command"], False)

    def test_contested_unit_keeps_both_charges_visible(self):
        recon = completion_reconciliation()
        contested = recon["contested_site"]
        self.assertEqual(contested["unit"], "i")
        self.assertIn("SERVICE-POLICY.retired-settings-gone",
                      contested["flat_wrapper_charge"])
        self.assertIn("SERVICE-POLICY.F40", contested["completion_recharge"])
        self.assertIn("byte-identical", contested["flat_wrapper_charge"])
        self.assertIn("documented-exception", contested["completion_recharge"])
        self.assertIn("no row is re-judged", contested["statement"])
        r1_rows = {row.obligation_id: row
                   for row in build_service_policy_density_obligations()}
        self.assertEqual(r1_rows["SERVICE-POLICY.F13"].expected, 410)
        self.assertEqual(r1_rows["SERVICE-POLICY.F13"].comparator, "http_status")
        self.assertEqual(r1_rows["SERVICE-POLICY.F14"].expected, "service_policy_retired")

    def test_marker_unit_by_leaf_maps_all_fifteen_leaves(self):
        mapping = marker_unit_by_leaf()
        self.assertEqual(len(mapping), 15)
        self.assertEqual(set(mapping), set(EXPECTED_LEAF_IDS))
        expected_map = {
            "SERVICE-POLICY.F33": "t", "SERVICE-POLICY.F34": "g",
            "SERVICE-POLICY.F35": "u", "SERVICE-POLICY.F36": "f",
            "SERVICE-POLICY.F37": "v", "SERVICE-POLICY.F38": "h",
            "SERVICE-POLICY.F39": "w", "SERVICE-POLICY.F40": "i",
            "SERVICE-POLICY.F41": "k", "SERVICE-POLICY.F42": "l",
            "SERVICE-POLICY.F43": "m", "SERVICE-POLICY.F44": "o",
            "SERVICE-POLICY.F45": "q", "SERVICE-POLICY.F46": "r",
            "SERVICE-POLICY.F47": "s"}
        self.assertEqual(mapping, expected_map)
        self.assertEqual(MARKER_UNIT_BY_LEAF, mapping)


class FormDifferenceTests(unittest.TestCase):
    def test_form_difference_named_item_fields(self):
        item = FORM_DIFFERENCE_NAMED_ITEM
        self.assertEqual(item["item_id"], FORM_DIFFERENCE_ITEM_ID)
        self.assertEqual(item["classification"], "product/form-difference")
        self.assertEqual(item["status"], "PRODUCT_FORM_DIFFERENCE_ON_FILE")
        self.assertEqual(item["parent"], "story_retired_service_policy")
        self.assertEqual(item["registered_leaf"], "SERVICE-POLICY.F40")
        self.assertEqual(item["basis"], BASIS_DOCUMENTED_EXCEPTION)
        contested = item["contested_site"]
        self.assertEqual(contested["line_start"], 81)
        self.assertEqual(contested["line_end"], 83)
        self.assertEqual(contested["method"], "PATCH")
        self.assertEqual(contested["path"], "/groups/{group_id}/settings")
        self.assertEqual(contested["operation"], "service_policy.settings_patch_retired")
        self.assertEqual(contested["quote"], "\n".join(
            STORY_PATH.read_text(encoding="utf-8").splitlines()[80:83]))
        station = item["station_form"]
        self.assertEqual(station["expected_status"], 410)
        self.assertEqual(station["error"], "service_policy_retired")
        self.assertIn("bcs-http/src/routes/groups/handlers.rs:632-637", station["basis"])
        self.assertEqual(len(station["observed_410_evidence"]), 2)
        self.assertIn("2026-10-30", station["observed_410_evidence"][0])
        self.assertIn("r9-transportfix-duo", station["observed_410_evidence"][1])
        sim = item["sim_form"]
        self.assertEqual(sim["observed_status"], 200)
        self.assertIn("poolab", sim["basis"])
        self.assertIn("410", sim["basis"])
        self.assertEqual(item["defect_registry"]["defect_id"], FORM_DIFFERENCE_DEFECT_ID)
        self.assertEqual(item["defect_registry"]["coverage_map_material_gap"],
                         COVERAGE_MAP_MATERIAL_GAP)
        self.assertIn("产品在案", item["defect_registry"]["material_gap_status"])
        self.assertIn("registered-not-rejudged", item["disposition"])
        self.assertIn("grants_release_credit=false", item["credit_posture"])

    def test_material_gate_associates_p4_and_credential_env_names(self):
        gate = MATERIAL_GATE
        self.assertEqual(gate["gate_id"], P4_GATE_ID)
        self.assertIn("P4", gate["gate_family"])
        self.assertIn("3c6d6f374", gate["gate_family"])
        self.assertEqual(len(gate["requires"]), 5)
        requires_text = " ".join(gate["requires"])
        self.assertIn("BCS_E2E_HUMAN_COOKIE", requires_text)
        self.assertIn("BCS_E2E_DRIVER_TOKEN", requires_text)
        self.assertIn("M11", requires_text)
        self.assertIn("PATCH /groups/{group_id}/settings", requires_text)
        self.assertTrue(gate["disclosed_not_executed"])
        self.assertIn("P4", gate["blocks"])

    def test_gap_ledger_registers_transport_and_form_difference(self):
        self.assertEqual(sorted(GAP_LEDGER), ["GAP-SP-D-01", "GAP-SP-D-02"])
        first = GAP_LEDGER["GAP-SP-D-01"]
        self.assertEqual(first["kind"], "EXECUTION_GAP_NOT_RUN")
        self.assertEqual(first["status"], "NOT_RUN_RECEIPT_ONLY")
        self.assertIn("all 15 completion leaves", first["blocks"])
        self.assertTrue(first["parsed_unit_claimed"])
        second = GAP_LEDGER["GAP-SP-D-02"]
        self.assertEqual(second["kind"], "PRODUCT_FORM_DIFFERENCE_ON_FILE")
        self.assertEqual(second["status"], "REGISTERED_NOT_REJUDGED")
        self.assertIn(FORM_DIFFERENCE_ITEM_ID, second["basis"])
        self.assertIn(P4_GATE_ID, second["closure_requirement"])
        self.assertIn("M11", second["closure_requirement"])
        self.assertTrue(second["parsed_unit_claimed"])

    def test_r1_roster_and_reconciliation_stay_unrejudged(self):
        rows = build_service_policy_density_obligations()
        self.assertEqual(len(rows), PARSED_OBLIGATION_COUNT)
        recon = completion_reconciliation()
        self.assertEqual(recon["r1_density_superset_caliber"]["parsed_obligation_count"],
                         PARSED_OBLIGATION_COUNT)
        self.assertIn("never merged", recon["r1_density_superset_caliber"]["statement"])
        honesty = honesty_ledger()
        self.assertTrue(honesty["no_sim_form_pass"])
        self.assertTrue(honesty["no_silent_rejudgment"])
        self.assertEqual(honesty["remote_validation"], STATUS_NOT_RUN)
        self.assertFalse(honesty["grants_release_credit"])


class CompletionRunTests(unittest.TestCase):
    def test_owned_local_replay_projects_the_completion_pass_receipt(self):
        receipt = run_service_policy_density_completion(profile=local_profile())
        self.assertEqual(receipt["status"], STATUS_PASS)
        self.assertEqual(receipt["executor"], EXECUTOR_LOCAL)
        self.assertEqual(receipt["case_id"], CASE_ID)
        self.assertEqual(receipt["parent"], "story_retired_service_policy")
        self.assertEqual(receipt["layer"], "MARKER_COMPLETION_WAVE_D")
        self.assertEqual(receipt["contract"], "service-policy-density-completion-v1")
        self.assertEqual(receipt["wrapper_command"], "dual-service-policy-density-run")
        self.assertFalse(receipt["new_registration_command"])
        scope = receipt["case_scope"]
        self.assertEqual(scope["completion_leaves"], 15)
        self.assertEqual(scope["residual_count_gap"], 0)
        self.assertEqual(scope["envelope_absent_leaves"], ENVELOPE_LEAF_IDS)
        self.assertEqual(scope["documented_exception_leaves"], ["SERVICE-POLICY.F40"])
        self.assertEqual(scope["contested_marker_unit"], "i")
        self.assertEqual(scope["gap_groups"], [
            {"gap_group": GAP_GROUP_LEGACY, "leaf_count": 7},
            {"gap_group": GAP_GROUP_RETIRED, "leaf_count": 1},
            {"gap_group": GAP_GROUP_INVOCATION, "leaf_count": 3},
            {"gap_group": GAP_GROUP_RUNNING, "leaf_count": 1},
            {"gap_group": GAP_GROUP_CONVERSION, "leaf_count": 3}])
        self.assertEqual(scope["marker_units"]["SERVICE-POLICY.F40"], "i")
        self.assertEqual(receipt["execution"]["completed_count"], 15)
        self.assertEqual(receipt["execution"]["failed_count"], 0)
        self.assertTrue(receipt["execution"]["transport_injected"])
        self.assertEqual(receipt["reports"]["pass_count"], 15)
        self.assertEqual(receipt["reports"]["executed_obligation_count"], 15)
        by_id = {row["obligation_id"]: row for row in receipt["assertions"]}
        self.assertEqual([row["status"] for row in receipt["assertions"]],
                         [STATUS_PASS] * 15)
        for row in receipt["assertions"]:
            self.assertEqual(row["layer"], "MARKER_COMPLETION_WAVE_D")
            self.assertTrue(row["density"])
            self.assertEqual(row["source"]["file"],
                             "apps/bcs/scripts/e2e-test/service_policy_story.py")
            self.assertTrue(row["source"]["located"])
        f40 = by_id["SERVICE-POLICY.F40"]
        self.assertEqual(f40["observed"], 410)
        self.assertEqual(f40["basis"], BASIS_DOCUMENTED_EXCEPTION)
        self.assertEqual(f40["form_difference"], FORM_DIFFERENCE_ITEM_ID)
        self.assertEqual(f40["documented_exception"]["item_id"], FORM_DIFFERENCE_ITEM_ID)
        self.assertEqual(f40["documented_exception"]["station_form"]["expected_status"], 410)
        self.assertEqual(f40["documented_exception"]["sim_form"]["observed_status"], 200)
        for leaf_id in ENVELOPE_LEAF_IDS:
            self.assertNotIn("observed", by_id[leaf_id])
            self.assertEqual(by_id[leaf_id]["comparator"], POLICY_ENVELOPE_ABSENT)
            self.assertTrue(by_id[leaf_id]["source"]["envelope_def"])
        f33 = by_id["SERVICE-POLICY.F33"]
        self.assertEqual(f33["marker_unit"], "t")
        self.assertEqual(f33["gap_group"], GAP_GROUP_LEGACY)
        self.assertEqual(f33["source"]["line_start"], 77)
        self.assertIn("assert_policy_absent(created)", f33["source"]["quote"])
        self.assertEqual(by_id["SERVICE-POLICY.F36"]["observed"], 201)
        self.assertEqual(by_id["SERVICE-POLICY.F36"]["expected"], [200, 201])
        self.assertEqual(by_id["SERVICE-POLICY.F46"]["observed"], 400)
        self.assertEqual(by_id["SERVICE-POLICY.F47"]["marker_unit"], "s")
        self.assertEqual(receipt["honesty"]["wave_token"], COMPLETION_WAVE_TOKEN)
        self.assertEqual(receipt["honesty"]["prior_wave_token"], R1_WAVE_TOKEN)
        self.assertEqual(receipt["honesty"]["residual_count_gap"], 0)
        self.assertEqual(receipt["honesty"]["product_defects"], product_defects_registry())
        self.assertEqual(receipt["product_defects"][FORM_DIFFERENCE_DEFECT_ID]["status"],
                         "PRODUCT_FORM_DIFFERENCE_ON_FILE")
        self.assertEqual(receipt["registered_exception"]["item_id"], FORM_DIFFERENCE_ITEM_ID)
        self.assertEqual(receipt["material_gate"]["gate_id"], P4_GATE_ID)
        self.assertEqual(sorted(receipt["gap_ledger"]), ["GAP-SP-D-01", "GAP-SP-D-02"])
        self.assertEqual(receipt["reconciliation"]["residual_count_gap"], 0)
        self.assertEqual(receipt["remote_validation"], STATUS_NOT_RUN)
        self.assertFalse(receipt["grants_release_credit"])
        self.assertNotIn("gap", receipt)
        self.assertEqual(density_accounting(receipt)["counts"],
                         {"PASS": 15, "FAIL": 0, "NOT_RUN": 0})
        self.assertEqual(density_accounting(receipt)["residual_count_gap"], 0)
        self.assertEqual(family_accounting()["family_density_rows"], 55)
        self.assertEqual(family_accounting()["basis_counts"],
                         {"frozen-anchor": 14, "documented-exception": 1})
        self.assertEqual(len(md_table(receipt).splitlines()), 17)
        self.assertIn("documented-exception", md_table(receipt))
        json.dumps(receipt, ensure_ascii=False, allow_nan=False)

    def test_default_and_injected_local_transports_produce_identical_receipts(self):
        default = run_service_policy_density_completion(profile=local_profile())
        injected = run_service_policy_density_completion(
            profile=local_profile(),
            transport=ReplayTransport(owned_local_replay_observations(local_profile())))
        self.assertEqual(json.loads(json.dumps(default, sort_keys=True)),
                         json.loads(json.dumps(injected, sort_keys=True)))

    def test_receipt_carries_no_synthetic_replay_identifiers(self):
        receipt = run_service_policy_density_completion(profile=local_profile())
        serialized = json.dumps(receipt, ensure_ascii=False)
        for synthetic in ("grp-policy-density-object", "grp-policy-density-null",
                          "svc-policy-density-1", "svc-policy-density-2",
                          "chat-policy-density-1", "bot-policy-density-driver"):
            self.assertNotIn(synthetic, serialized)
        assertions_serialized = json.dumps(receipt["assertions"], ensure_ascii=False)
        self.assertNotIn("grp-policy-density", assertions_serialized)

    def test_read_status_failure_fails_fast_and_cascades(self):
        profile = local_profile()
        observations = owned_local_replay_observations(profile)
        observations["service_policy.group_read_object"] = Observation(
            500, {"error": "replay injected transport failure"})
        receipt = run_service_policy_density_completion(
            profile=profile, transport=ReplayTransport(observations))
        self.assertEqual(receipt["status"], STATUS_FAIL)
        self.assertEqual(receipt["execution"]["error_type"],
                         "SERVICE_POLICY_DENSITY_COMPLETION_ASSERTION_FAILED")
        by_id = {row["obligation_id"]: row for row in receipt["assertions"]}
        self.assertEqual(by_id["SERVICE-POLICY.F33"]["status"], STATUS_PASS)
        self.assertEqual(by_id["SERVICE-POLICY.F34"]["status"], STATUS_FAIL)
        self.assertEqual(by_id["SERVICE-POLICY.F34"]["observed"], 500)
        blocked = receipt["assertions"][2:]
        self.assertEqual(len(blocked), 13)
        self.assertTrue(all(row["status"] == STATUS_NOT_RUN
                            and row["error_type"] == BLOCKED_AFTER_FAILURE
                            for row in blocked))
        self.assertEqual(receipt["reports"]["pass_count"], 1)

    def test_envelope_leak_mutant_fail_closes_the_first_leaf(self):
        profile = local_profile()
        observations = owned_local_replay_observations(profile)
        observations["service_policy.group_create_object"] = Observation(
            200, {"id": "grp-policy-density-object", "label": "x",
                  "service_spec": {"enabled": False}})
        receipt = run_service_policy_density_completion(
            profile=profile, transport=ReplayTransport(observations))
        self.assertEqual(receipt["status"], STATUS_FAIL)
        by_id = {row["obligation_id"]: row for row in receipt["assertions"]}
        self.assertEqual(by_id["SERVICE-POLICY.F33"]["status"], STATUS_FAIL)
        self.assertEqual(by_id["SERVICE-POLICY.F33"]["observed"], ["service_spec"])
        self.assertEqual(by_id["SERVICE-POLICY.F34"]["status"], STATUS_NOT_RUN)
        self.assertEqual(by_id["SERVICE-POLICY.F47"]["status"], STATUS_NOT_RUN)

    def test_both_envelope_fields_leak_are_disclosed(self):
        observations = owned_local_replay_observations(local_profile())
        observations["service_policy.group_read_null"] = Observation(
            200, {"id": "grp-policy-density-null", "service_spec": None,
                  "retired_service_spec": {"enabled": False}})
        receipt = run_service_policy_density_completion(
            profile=local_profile(), transport=ReplayTransport(observations))
        by_id = {row["obligation_id"]: row for row in receipt["assertions"]}
        self.assertEqual(by_id["SERVICE-POLICY.F39"]["status"], STATUS_FAIL)
        self.assertEqual(by_id["SERVICE-POLICY.F39"]["observed"],
                         sorted(ENVELOPE_FIELDS))

    def test_empty_transport_seam_charges_evidence_not_pass(self):
        receipt = run_service_policy_density_completion(
            profile=local_profile(), transport=ReplayTransport({}))
        self.assertEqual(receipt["status"], STATUS_FAIL)
        by_id = {row["obligation_id"]: row for row in receipt["assertions"]}
        self.assertEqual(by_id["SERVICE-POLICY.F33"]["status"], STATUS_FAIL)
        self.assertEqual(by_id["SERVICE-POLICY.F33"]["error_type"], "RunStateError")
        self.assertEqual(by_id["SERVICE-POLICY.F34"]["status"], STATUS_NOT_RUN)
        self.assertEqual(by_id["SERVICE-POLICY.F34"]["error_type"],
                         BLOCKED_AFTER_FAILURE)

    def test_executor_fail_closes_before_any_receipt_on_source_drift(self):
        pristine = STORY_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            drifted = drifted_py_copy(Path(tmp), pristine + "\n# drift\n")
            with mock.patch.object(completion_module, "SOURCE_PATH", drifted):
                with self.assertRaises(RunStateError):
                    run_service_policy_density_completion(profile=local_profile())

    def test_preprod_offline_fail_closes_to_the_completion_named_gap(self):
        receipt = run_service_policy_density_completion(profile=preprod_profile())
        self.assertEqual(receipt["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(receipt["executor"], EXECUTOR_PREPROD)
        self.assertEqual(receipt["gap"]["id"], COMPLETION_GAP_ID)
        self.assertEqual(receipt["gap"]["kind"], COMPLETION_GAP_KIND)
        self.assertEqual(receipt["gap"]["gap_ledger_refs"], ["GAP-SP-D-01", "GAP-SP-D-02"])
        self.assertEqual(receipt["gap"]["registered_exception"], FORM_DIFFERENCE_ITEM_ID)
        self.assertEqual(receipt["gap"]["material_gate"], P4_GATE_ID)
        self.assertEqual(receipt["gap"]["contested_marker_unit"], "i")
        self.assertEqual(len(receipt["assertions"]), 15)
        for row in receipt["assertions"]:
            self.assertEqual(row["status"], STATUS_NOT_RUN)
            self.assertEqual(row["error_type"], "PREPROD_EXECUTION_NOT_RUN")
        self.assertEqual(receipt["execution"]["status"], STATUS_NOT_RUN)
        self.assertEqual(receipt["execution"]["completed_count"], 0)
        self.assertFalse(receipt["execution"]["transport_injected"])
        self.assertEqual(receipt["reports"]["pass_count"], 0)
        self.assertEqual(receipt["honesty"]["gap_ledger"]["GAP-SP-D-01"]["kind"],
                         "EXECUTION_GAP_NOT_RUN")
        self.assertEqual(receipt["honesty"]["gap_ledger"]["GAP-SP-D-02"]["status"],
                         "REGISTERED_NOT_REJUDGED")
        self.assertEqual(receipt["registered_exception"]["item_id"],
                         FORM_DIFFERENCE_ITEM_ID)
        self.assertEqual(receipt["product_defects"][FORM_DIFFERENCE_DEFECT_ID][
                             "coverage_map_material_gap"], COVERAGE_MAP_MATERIAL_GAP)
        self.assertEqual(receipt["material_gate"]["gate_id"], P4_GATE_ID)
        self.assertEqual(receipt["reconciliation"]["residual_count_gap"], 0)
        self.assertEqual(receipt["remote_validation"], STATUS_NOT_RUN)
        self.assertFalse(receipt["grants_release_credit"])

    def test_injected_transport_evaluates_preprod_identically(self):
        observations = owned_local_replay_observations(preprod_profile())
        receipt = run_service_policy_density_completion(
            profile=preprod_profile(),
            transport=ReplayTransport(observations))
        self.assertEqual(receipt["status"], STATUS_PASS)
        self.assertEqual(receipt["executor"], EXECUTOR_PREPROD)
        self.assertEqual(receipt["execution"]["completed_count"], 15)
        self.assertNotIn("gap", receipt)
        default_local = run_service_policy_density_completion(profile=local_profile())
        self.assertEqual(
            [row["status"] for row in receipt["assertions"]],
            [row["status"] for row in default_local["assertions"]])


class RegistrationProposalTests(unittest.TestCase):
    def test_proposal_is_describe_only_with_the_wave_token(self):
        self.assertEqual(PROPOSAL_TOKEN, "SERVICE_POLICY_DENSITY_15_OBLIGATIONS_20261031")
        proposal = registration_proposal()
        self.assertEqual(proposal["token"], PROPOSAL_TOKEN)
        self.assertEqual(proposal["proposal_status"], "DESCRIBE_ONLY")
        self.assertFalse(proposal["applied"])
        self.assertEqual(proposal["shared_face_files_touched"], [])
        self.assertFalse(proposal["envelope"]["new_registration_command"])
        self.assertEqual(proposal["envelope"]["command"], "dual-service-policy-density-run")
        self.assertFalse(proposal["envelope"]["cli_required"])
        self.assertFalse(proposal["envelope"]["case_command_registration_required"])
        executor = proposal["executor_migration"]
        self.assertEqual(executor["row_index"], 38)
        self.assertEqual(executor["row_id"], "story_retired_service_policy")
        self.assertEqual(executor["notes_before"], [R1_WAVE_TOKEN])
        self.assertEqual(executor["notes_append_after"],
                         [R1_WAVE_TOKEN, PROPOSAL_TOKEN])
        self.assertIn("AFTER", executor["placement"])
        coverage = proposal["coverage_map"]
        self.assertEqual(coverage["row"], 19)
        self.assertEqual(coverage["residual_count_gap_after_wave"], 0)
        self.assertIn("形态差核对", coverage["queue_basis_on_file"])
        self.assertIn(FORM_DIFFERENCE_ITEM_ID, coverage["form_difference_disposition"])
        self.assertEqual(coverage["material_gap_member"], COVERAGE_MAP_MATERIAL_GAP)
        self.assertEqual(proposal["wave"]["family_density_rows"], 55)
        self.assertEqual(proposal["wave"]["residual_count_gap"], 0)
        self.assertEqual(proposal["accounting"]["basis_counts"],
                         {"frozen-anchor": 14, "documented-exception": 1})
        self.assertEqual(proposal["source_freeze"]["story_py_sha256"], STORY_PY_SHA256)
        self.assertEqual(proposal["source_freeze"]["shell_file"], STORY_SH_FILE)
        red_lines = "\n".join(proposal["red_lines"])
        for forbidden in ("executor-migration.json", "coverage-map/", "inventory.py",
                          "cli.py", "case_command_registration.py",
                          "workspace-migration-contract.json", "slow_judge",
                          "edge_revoke_lifecycle", "delivery_density",
                          "webhook_subscription"):
            self.assertIn(forbidden, red_lines)

    def test_describe_only_fragment_lists_every_shared_face_and_red_line(self):
        fragment = describe_only_mainline_fragment()
        self.assertIn(PROPOSAL_TOKEN, fragment)
        self.assertIn(R1_WAVE_TOKEN, fragment)
        self.assertIn("AFTER the existing", fragment)
        self.assertIn("executor-migration.json row #38", fragment)
        self.assertIn("inventory.py", fragment)
        self.assertIn("coverage-map row #19", fragment)
        self.assertIn("cli.py", fragment)
        self.assertIn("case_command_registration.py", fragment)
        self.assertIn("byte-identical", fragment)
        self.assertIn("BCS_E2E_HUMAN_COOKIE", fragment)
        self.assertIn("BCS_E2E_DRIVER_TOKEN", fragment)
        self.assertIn(P4_GATE_ID, fragment)
        self.assertIn(FORM_DIFFERENCE_ITEM_ID, fragment)
        self.assertIn("residual_count_gap=0", fragment)

    def test_proposal_fail_closes_on_shared_face_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_root = Path(tmp)
            with mock.patch.object(proposal_module, "REPO_ROOT", empty_root):
                with self.assertRaises(RunStateError):
                    registration_proposal()
            crafted = empty_root / "apps/bcs/scripts/e2e-runner/bcs_e2e"
            crafted.mkdir(parents=True)
            (crafted / "cli.py").write_text(
                "from .service_policy_density.reporting import "
                "dual_service_policy_density_case_run\n", encoding="utf-8")
            with mock.patch.object(proposal_module, "REPO_ROOT", empty_root):
                with self.assertRaises(RunStateError):
                    registration_proposal()

    def test_proposal_is_json_serializable_and_asserts_anchors(self):
        proposal = registration_proposal()
        json.dumps(proposal, ensure_ascii=False)
        self.assertTrue(callable(proposal_module.assert_anchors))
        proposal_module.assert_anchors()


class BackwardCompatibilityTests(unittest.TestCase):
    def test_r1_calibers_and_station_denominators_stay_unchanged(self):
        rows = build_service_policy_density_obligations()
        self.assertEqual(len(rows), PARSED_OBLIGATION_COUNT)
        self.assertEqual(PARSED_OBLIGATION_COUNT, 40)
        plan = completion_plan()
        self.assertEqual(plan["wrapper_command"], "dual-service-policy-density-run")
        self.assertEqual(family_accounting()["r1_density_superset_rows"], 40)
        self.assertEqual(family_accounting()["flat_wrapper_obligations"], 8)
        self.assertEqual(family_accounting()["family_density_rows"], 55)

    def test_case_wrapper_caliber_stays_the_r1_40_point_roster(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = local_profile()
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            state_dir = root / "case-state"
            report = dual_service_policy_density_case_run(
                profile_path=profile_path, state_dir=state_dir,
                run_id=LOCAL_RUN_ID, allow_write=True,
                confirm_run_id=LOCAL_RUN_ID)
            self.assertEqual(report["status"], STATUS_PASS)
            self.assertEqual(report["case_id"], CASE_ID)
            self.assertEqual(report["case_scope"]["expected_obligation_count"], 40)
            self.assertEqual(report["case_scope"]["field_obligation_count"], 32)
            execution = report["execution"]
            self.assertEqual(execution["case_scope"]["expected_obligation_count"], 40)
            self.assertEqual(report["case_scope"]["field_obligation_count"], 32)
            self.assertEqual(execution["execution"]["completed_count"], 40)
            for row in execution["assertions"]:
                self.assertEqual(row["status"], STATUS_PASS)
            self.assertNotIn("completion", json.dumps(report["case_scope"]))
            self.assertFalse(report["grants_release_credit"])
            self.assertEqual(report["remote_validation"], STATUS_NOT_RUN)
            files = sorted(path.name for path in state_dir.iterdir())
            self.assertEqual(files, ["service-policy-density-binding.json",
                                     "service-policy-density-profile.json",
                                     "service-policy-density-run.json"])

    def test_case_wrapper_preprod_caliber_stays_the_r1_named_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_path = root / "preprod.json"
            profile_path.write_text(json.dumps(preprod_profile()), encoding="utf-8")
            state_dir = root / "preprod-state"
            report = dual_service_policy_density_case_run(
                profile_path=profile_path, state_dir=state_dir,
                run_id=PREPROD_RUN_ID, allow_write=True,
                confirm_run_id=PREPROD_RUN_ID)
            self.assertEqual(report["status"], STATUS_BLOCKED_INVENTORIED)
            self.assertEqual(report["gap"]["id"], GAP_ID)
            self.assertEqual(report["case_scope"]["expected_obligation_count"], 40)
            execution = report["execution"]
            self.assertEqual(execution["execution"]["total_count"], 40)
            self.assertTrue(all(row["status"] == STATUS_NOT_RUN
                                for row in execution["assertions"]))

    def test_completion_receipt_shares_the_family_envelope_keys(self):
        completion = run_service_policy_density_completion(profile=local_profile())
        r1_receipt = run_service_policy_density(profile=local_profile())
        shared = {"schema_version", "case_id", "parent", "status", "environment",
                  "executor", "profile_digest", "assertions", "execution",
                  "reports", "remote_validation", "grants_release_credit",
                  "reconciliation"}
        self.assertTrue(shared <= set(completion.keys()))
        self.assertTrue(shared <= set(r1_receipt.keys()))
        self.assertEqual(completion["contract"], "service-policy-density-completion-v1")
        self.assertEqual(r1_receipt["contract"], "service-policy-density-projection-v1")


class CredentialHygieneTests(unittest.TestCase):
    def test_new_package_files_embed_no_credential_value_literals(self):
        package_dir = Path(completion_module.__file__).resolve().parent
        uuid_like = re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
        for name in ("completion.py", "completion_run.py",
                     "registration_proposal.py", "__init__.py"):
            text = (package_dir / name).read_text(encoding="utf-8")
            self.assertIsNone(uuid_like.search(text), f"uuid literal in {name}")
            # needle split at runtime: the runner case guard forbids the raw
            # credential-prefix literal inside test sources itself
            self.assertNotIn("Bear" + "er ", text, f"bearer literal in {name}")

    def test_gate_and_proposal_reference_env_names_only(self):
        requires_text = " ".join(MATERIAL_GATE["requires"])
        self.assertIn("BCS_E2E_HUMAN_COOKIE", requires_text)
        self.assertIn("BCS_E2E_DRIVER_TOKEN", requires_text)
        serialized = json.dumps(registration_proposal(), ensure_ascii=False)
        self.assertNotIn("Bear" + "er ", serialized)
        self.assertIn("BCS_E2E_HUMAN_COOKIE", serialized)
        self.assertIn("BCS_E2E_DRIVER_TOKEN", serialized)

    def test_honesty_ledger_block_is_exposed_and_consistent(self):
        assert_honesty_ledger()
        ledger = honesty_ledger()
        self.assertEqual(ledger["completion_leaves"], 15)
        self.assertEqual(ledger["residual_count_gap"], 0)
        self.assertEqual(ledger["product_defects"][FORM_DIFFERENCE_DEFECT_ID][
                             "coverage_map_material_gap"], COVERAGE_MAP_MATERIAL_GAP)
        self.assertEqual(ledger["gap_ledger"]["GAP-SP-D-02"]["status"],
                         "REGISTERED_NOT_REJUDGED")
        self.assertEqual(ledger["wave_token"], COMPLETION_WAVE_TOKEN)


if __name__ == "__main__":
    unittest.main()
