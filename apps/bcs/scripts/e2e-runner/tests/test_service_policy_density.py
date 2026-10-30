"""Focused offline tests for the retired service policy density slice.

Covers the source-anchored obligation builder (eight coarse parents aligned
with the frozen flat ``SERVICE-POLICY.*`` contract plus thirty-two field
obligations projected from the thirty-two anchored upstream field sites), the
strict dual-tier profile admission (single-driver journey, nineteen reviewed
station bindings, derived teardown disclosed but never bound), the owned-local
offline full-PASS演算, the fail-fast and tier-honesty disqualifiers, the
preprod named-gap BLOCKED_INVENTORIED fail-close, the injected-transport seam
evaluating both tiers under identical PASS criteria, and the unregistered
wrapper's confirmation gate and honest receipt triple.  No socket is opened
and no credential value is resolved or echoed anywhere in this module.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.local_service_policy_contract import (
    REQUIREMENTS,
    service_policy_source_binding,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import RunStateError
from bcs_e2e.service_policy_density import (
    CASE_ID,
    COARSE_PARENT_COUNT,
    DENSITY_OPERATION_ROWS,
    DUAL_SERVICE_POLICY_DENSITY_COMMAND,
    FIELD_OBLIGATION_COUNT,
    FIELD_SITE_COUNT,
    GAP_ID,
    LOCAL_TEARDOWN_DISPOSITIONS,
    NOT_CHARGED_SOURCE_PROBES,
    PARSED_OBLIGATION_COUNT,
    PARENT,
    PREPROD_TEARDOWN_DISPOSITIONS,
    STATUS_BLOCKED_INVENTORIED,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    Observation,
    ReplayTransport,
    build_service_policy_density_obligations,
    dual_service_policy_density_case_run,
    owned_local_replay_observations,
    owned_local_replay_transport,
    run_service_policy_density,
    service_policy_density_credential_kind,
    service_policy_density_plan,
    service_policy_density_reconciliation,
    validate_service_policy_density_profile,
)
from bcs_e2e.service_policy_density.contract import (
    BOOTSTRAP_OPERATION_ROWS,
    BUSINESS_OPERATION_ROWS,
    REPORT_CONTRACT,
    TEARDOWN_DERIVED_OPERATION,
)
from bcs_e2e.service_policy_density.obligations import (
    BEARER_STORY_CHECKS,
    MARKER_CHARGED_CHECKS,
    SOURCE_FILE,
)
from bcs_e2e.service_policy_density.profile import PROFILE_CONTRACT
from bcs_e2e.service_policy_density.reporting import (
    RESOURCE_DISPOSITION,
    dual_service_policy_density_case_run as case_run,
)
from bcs_e2e.service_policy_density.run import (
    CHAT_SESSION_ID,
    TEARDOWN_FENCE,
    GROUP_NULL_ID,
    GROUP_OBJECT_ID,
    INVOCATION_CREATE_OPERATIONS,
    INVOCATION_FIRST_SESSION_ID,
    INVOCATION_SECOND_SESSION_ID,
)
import bcs_e2e.service_policy_density.obligations as obligations_module

STORY_PATH = ROOT.parent / "e2e-test" / "service_policy_story.py"
LOCAL_RUN_ID = "20261031-service-policy-density-local"
PREPROD_RUN_ID = "20261031-service-policy-density-preprod"


def local_profile() -> dict:
    return validate_service_policy_density_profile({
        "schema_version": 1,
        "contract": PROFILE_CONTRACT,
        "environment": {"tier": "local", "id": "service-policy-density-local",
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
    profile["environment"] = {"tier": "preprod", "id": "bcs-pre-service-policy-density",
                              "expected_bcs_env": "pre"}
    profile["entrypoint"] = {"kind": "bcs-direct", "origin": "https://bcs-pre.alipay.com",
                             "api_face": "legacy", "manifest_face": "legacy"}
    profile["auth"] = {"kind": "human-cookie-with-owned-bot-tokens",
                       "driver_token_env": "BCS_E2E_DRIVER_TOKEN",
                       "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
    return validate_service_policy_density_profile(profile)


class ServicePolicyDensityObligationTests(unittest.TestCase):
    def test_builder_projects_eight_parents_and_thirty_two_field_obligations(self):
        rows = build_service_policy_density_obligations()
        self.assertEqual(len(rows), PARSED_OBLIGATION_COUNT)
        parents = [row for row in rows if row.level == "PARENT"]
        fields = [row for row in rows if row.level == "FIELD"]
        self.assertEqual(len(parents), COARSE_PARENT_COUNT)
        self.assertEqual(len(fields), FIELD_OBLIGATION_COUNT)
        self.assertEqual(FIELD_SITE_COUNT, FIELD_OBLIGATION_COUNT)
        self.assertEqual([row.obligation_id for row in parents], list(REQUIREMENTS))
        self.assertEqual([row.obligation_id for row in fields],
                         [f"SERVICE-POLICY.F{index:02d}" for index in range(1, 33)])
        parent_ids = {row.obligation_id for row in parents}
        self.assertTrue(all(row.parent_obligation in parent_ids for row in fields))
        self.assertEqual(len({row.obligation_id for row in rows}), PARSED_OBLIGATION_COUNT)

    def test_parents_and_fields_carry_the_reviewed_shapes(self):
        rows = build_service_policy_density_obligations()
        parents = {row.obligation_id: row for row in rows if row.level == "PARENT"}
        fields = [row for row in rows if row.level == "FIELD"]
        for row in parents.values():
            self.assertFalse(row.density)
            self.assertIsNone(row.field)
            self.assertEqual(row.kind, "status")
            self.assertIn(row.comparator, {"http_status", "http_status_enum",
                                           "teardown_dispositions"})
            self.assertTrue(row.expectation)
        self.assertEqual(parents["SERVICE-POLICY.cleanup-disposition"].comparator,
                         "teardown_dispositions")
        self.assertEqual(parents["SERVICE-POLICY.legacy-encoding-ignored"].comparator,
                         "http_status_enum")
        self.assertEqual(parents["SERVICE-POLICY.legacy-encoding-ignored"].expected,
                         (200, 201))
        self.assertEqual(parents["SERVICE-POLICY.mock-human-bootstrap"].caller, "human")
        self.assertEqual(parents["SERVICE-POLICY.provider-free-bot-registration"].caller,
                         "human")
        for row in fields:
            self.assertTrue(row.density)
            self.assertEqual(row.kind, "field")
            self.assertEqual(row.caller, "driver")
            self.assertIsNotNone(row.comparator_text)
        self.assertEqual({row.comparator for row in fields},
                         {"http_status", "http_status_enum", "field_equals",
                          "field_absent", "field_contains", "session_ids_distinct"})
        self.assertEqual({row.family for row in rows},
                         {"BOOTSTRAP", "LEGACY", "RETIRED", "INVOCATION", "GUARD",
                          "CONVERSION", "TEARDOWN"})

    def test_preprod_modes_are_discharged_honestly(self):
        rows = build_service_policy_density_obligations()
        by_id = {row.obligation_id: row for row in rows}
        self.assertEqual(by_id[REQUIREMENTS[0]].preprod_mode, "BEARER_IDENTITY_CONFIRM")
        self.assertEqual(by_id[REQUIREMENTS[1]].preprod_mode,
                         "REMOTE_NOT_APPLICABLE_PRE_ADMITTED_DRIVER")
        for row in rows[2:]:
            self.assertEqual(row.preprod_mode, "LIVE_BEARER_REPLAY")

    def test_absence_checks_split_into_eight_field_children(self):
        rows = build_service_policy_density_obligations()
        absence = [row for row in rows if row.comparator == "field_absent"]
        self.assertEqual(len(absence), 8)
        self.assertEqual({row.field for row in absence},
                         {"service_spec", "retired_service_spec"})
        encoding_iterations = {row.operation for row in absence}
        self.assertEqual(encoding_iterations,
                         {"service_policy.group_create_object",
                          "service_policy.group_read_object",
                          "service_policy.group_create_null",
                          "service_policy.group_read_null"})

    def test_every_obligation_anchors_to_a_live_story_line(self):
        lines = STORY_PATH.read_text(encoding="utf-8").splitlines()
        plan = service_policy_density_plan()
        self.assertEqual(plan["parent"], PARENT)
        self.assertEqual(plan["source_file"], SOURCE_FILE)
        self.assertTrue(plan["source_read_only"])
        located = 0
        for row in plan["obligations"]:
            self.assertTrue(row["located"])
            self.assertEqual(row["source_file"], SOURCE_FILE)
            self.assertGreaterEqual(row["source_line"], 1)
            self.assertLessEqual(row["source_line"], len(lines))
            self.assertIn(row["source_quote"], lines[row["source_line"] - 1])
            self.assertIn(row["request_quote"], lines[row["request_line"] - 1])
            self.assertTrue(row["source_quote"])
            self.assertTrue(row["request_quote"])
            located += 1
        self.assertEqual(located, PARSED_OBLIGATION_COUNT)
        self.assertEqual(len(plan["not_charged_source_probes"]),
                         len(NOT_CHARGED_SOURCE_PROBES))
        for probe in plan["not_charged_source_probes"]:
            self.assertIn(probe["source_quote"], lines[probe["source_line"] - 1])
            self.assertTrue(probe["reason"])

    def test_parents_align_one_to_one_with_the_frozen_flat_contract(self):
        binding = service_policy_source_binding()
        self.assertEqual(binding["parent"], PARENT)
        self.assertEqual(binding["story_checks"], MARKER_CHARGED_CHECKS)
        self.assertEqual(binding["story_checks"], 23)
        self.assertEqual([row["id"] for row in binding["requirements"]],
                         list(REQUIREMENTS))
        rows = {row.obligation_id: row for row in build_service_policy_density_obligations()
                if row.level == "PARENT"}
        story_lines = {row["id"]: row["story_lines"] for row in binding["requirements"]}
        for requirement_id, row in rows.items():
            window = story_lines[requirement_id]
            self.assertGreaterEqual(row.source_line, window[0])
            self.assertLessEqual(row.source_line, window[1])
            self.assertGreaterEqual(row.request_line, window[0])
            self.assertLessEqual(row.request_line, window[1])
            self.assertEqual(binding["requirements"][list(rows).index(requirement_id)]["markers"],
                             ["retired-service-policy-compliance"])

    def test_roster_operations_land_on_the_reviewed_nineteen_stations(self):
        rows = build_service_policy_density_obligations()
        stations = {row["operation"] for row in DENSITY_OPERATION_ROWS}
        self.assertEqual(len(DENSITY_OPERATION_ROWS), 19)
        self.assertEqual(len(stations), 19)
        self.assertEqual({row["operation"] for row in BOOTSTRAP_OPERATION_ROWS},
                         {"service_policy.ensure_human", "service_policy.register_token",
                          "service_policy.register_bot", "service_policy.onboard_bot"})
        self.assertEqual(len(BUSINESS_OPERATION_ROWS), 15)
        field_operations = {row.operation for row in rows if row.level == "FIELD"}
        self.assertEqual(field_operations,
                         {row["operation"] for row in BUSINESS_OPERATION_ROWS})
        roster_operations = {row.operation for row in rows
                             if row.operation != "service_policy.teardown_dispositions"}
        self.assertTrue(roster_operations <= stations)
        plan = service_policy_density_plan()
        self.assertEqual(plan["station_operation_count"], 19)
        self.assertEqual(plan["business_operation_count"], 15)
        disclosed = plan["disclosed_non_binding_operations"]
        self.assertEqual(len(disclosed), 1)
        self.assertEqual(disclosed[0]["operation"], "service_policy.teardown_dispositions")
        self.assertEqual(disclosed[0]["method"], "PROJECTED")

    def test_reconciliation_discloses_every_parsed_caliber_honestly(self):
        reconciliation = service_policy_density_reconciliation()
        self.assertEqual(reconciliation["coverage_map_snapshot_parsed_count"], 23)
        self.assertEqual(reconciliation["bearer_leg_story_checks"], BEARER_STORY_CHECKS)
        self.assertEqual(reconciliation["bearer_leg_story_checks"], 20)
        self.assertEqual(reconciliation["marker_charged_checks"], MARKER_CHARGED_CHECKS)
        self.assertEqual(reconciliation["raw_status_sites"], COARSE_PARENT_COUNT)
        self.assertEqual(reconciliation["raw_field_sites"], FIELD_OBLIGATION_COUNT)
        self.assertEqual(reconciliation["raw_anchored_sites"], PARSED_OBLIGATION_COUNT)
        self.assertEqual(reconciliation["projected_parsed_total"], PARSED_OBLIGATION_COUNT)
        self.assertEqual(reconciliation["located_total"], PARSED_OBLIGATION_COUNT)
        self.assertEqual(reconciliation["located_parents"], COARSE_PARENT_COUNT)
        self.assertEqual(reconciliation["located_fields"], FIELD_OBLIGATION_COUNT)
        self.assertEqual(reconciliation["parsed_unlocated_count"], 0)
        self.assertEqual(reconciliation["parsed_unlocated"], [])
        self.assertFalse(reconciliation["upstream_native_assertion_ids"])
        self.assertIn("410", reconciliation["poolab_form_difference_note"])
        self.assertIn("story_retired_service_policy", reconciliation["coverage_map_row"])
        self.assertIn("compound", reconciliation["projection_rule"])

    def test_builder_fail_closes_on_anchor_drift(self):
        rows = build_service_policy_density_obligations()
        self.assertTrue(all(row.located for row in rows))
        pristine = STORY_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            drifted = Path(tmp) / "service_policy_story.py"
            drifted.write_text(pristine.replace("service_policy_retired",
                                                "service_policy_returned"),
                               encoding="utf-8")
            with mock.patch.object(obligations_module, "SOURCE_PATH", drifted):
                with self.assertRaises(RunStateError):
                    build_service_policy_density_obligations()
            header_drift = Path(tmp) / "header_drift.py"
            header_drift.write_text(
                pristine.replace("    def run(self) -> None:", "    def run(self):"),
                encoding="utf-8")
            with mock.patch.object(obligations_module, "SOURCE_PATH", header_drift):
                with self.assertRaises(RunStateError):
                    build_service_policy_density_obligations()
            intact = Path(tmp) / "intact.py"
            intact.write_text(pristine, encoding="utf-8")
            with mock.patch.object(obligations_module, "SOURCE_PATH", intact):
                self.assertEqual(len(build_service_policy_density_obligations()),
                                 PARSED_OBLIGATION_COUNT)

    def test_package_embeds_no_credential_value_literals(self):
        package_dir = Path(obligations_module.__file__).resolve().parent
        uuid_like = re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
        for path in sorted(package_dir.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(uuid_like.search(text), f"uuid literal in {path.name}")
            self.assertNotIn("Bearer ", text, f"bearer literal in {path.name}")


class ServicePolicyDensityProfileTests(unittest.TestCase):
    def test_admits_both_tiers_and_freezes_the_nineteen_station_bindings(self):
        local = local_profile()
        preprod = preprod_profile()
        self.assertEqual(local["environment"]["expected_bcs_env"], "local")
        self.assertEqual(preprod["environment"]["expected_bcs_env"], "pre")
        self.assertEqual(local["entrypoint"]["kind"], "singlebox")
        self.assertEqual(preprod["entrypoint"]["kind"], "bcs-direct")
        self.assertEqual(local["entrypoint"]["api_face"], "legacy")
        self.assertEqual(preprod["entrypoint"]["api_face"], "legacy")
        self.assertEqual(local["auth"]["kind"], "owned-local-bot-tokens")
        self.assertEqual(preprod["auth"]["kind"], "human-cookie-with-owned-bot-tokens")
        self.assertEqual(service_policy_density_credential_kind(local),
                         "owned_local_bot_token")
        self.assertEqual(service_policy_density_credential_kind(preprod),
                         "human_cookie_with_owned_driver_bot_token")
        self.assertEqual(list(local["actors"]), ["driver"])
        self.assertEqual(local["actors"]["driver"]["bot_id"],
                         "service-policy-density-driver")
        self.assertEqual({row["operation"] for row in DENSITY_OPERATION_ROWS},
                         set(local["route_bindings"]))
        self.assertNotIn(TEARDOWN_DERIVED_OPERATION, local["route_bindings"])
        self.assertNotIn("member", local["actors"])
        self.assertEqual({key for key in local["auth"]}, {"kind", "driver_token_env"})
        self.assertEqual({key for key in preprod["auth"]},
                         {"kind", "driver_token_env", "cookie_env"})
        for row in DENSITY_OPERATION_ROWS:
            self.assertEqual(set(row), {"requirement_id", "operation", "method",
                                        "path", "caller"})

    def test_rejects_schema_contract_and_key_set_drift(self):
        mutations = (
            lambda p: p.update({"schema_version": 2}),
            lambda p: p.update({"contract": "service-policy-density-profile-v2"}),
            lambda p: p.update({"secrets": {"note": "inline-value-forbidden"}}),
            lambda p: p["environment"].update({"extra": "x"}),
            lambda p: p["route_bindings"].update({
                "service_policy.unknown_op": {"method": "GET",
                                              "path": "/unknown", "api_face": "legacy"}}),
        )
        for mutate in mutations:
            profile = local_profile()
            mutate(profile)
            with self.assertRaises(RunStateError):
                validate_service_policy_density_profile(profile)
        profile = local_profile()
        profile["route_bindings"].pop("service_policy.group_read_object")
        with self.assertRaises(RunStateError):
            validate_service_policy_density_profile(profile)

    def test_rejects_tier_entrypoint_face_and_origin_drift(self):
        local_mutations = (
            lambda p: p["entrypoint"].update({"kind": "gateway"}),
            lambda p: p["entrypoint"].update({"origin": "https://127.0.0.1:18099"}),
            lambda p: p["entrypoint"].update({"origin": "http://127.0.0.1"}),
            lambda p: p["entrypoint"].update({"api_face": "public-v1"}),
            lambda p: p["entrypoint"].update({"manifest_face": "internal-v1"}),
            lambda p: p["environment"].update({"tier": "station"}),
            lambda p: p["environment"].update({"expected_bcs_env": "pre"}),
        )
        for mutate in local_mutations:
            profile = local_profile()
            mutate(profile)
            with self.assertRaises(RunStateError):
                validate_service_policy_density_profile(profile)
        preprod_mutations = (
            lambda p: p["entrypoint"].update({"kind": "singlebox"}),
            lambda p: p["entrypoint"].update({"origin": "http://bcs-pre.alipay.com"}),
            lambda p: p["environment"].update({"expected_bcs_env": "local"}),
            lambda p: p["auth"].update({"kind": "owned-local-bot-tokens"}),
        )
        for mutate in preprod_mutations:
            profile = preprod_profile()
            mutate(profile)
            with self.assertRaises(RunStateError):
                validate_service_policy_density_profile(profile)

    def test_rejects_credential_posture_identity_and_station_binding_drift(self):
        mutations = (
            lambda p: p["auth"].update({"driver_token_env": "bcs_e2e_driver_token"}),
            lambda p: p["auth"].update({"driver_token_env": "BC"}),
            lambda p: p["auth"].update({"cookie_env": "bcs_e2e_human_cookie"}),
            lambda p: p["auth"].pop("cookie_env"),
            lambda p: p["auth"].update({"member_token_env": "BCS_E2E_MEMBER_TOKEN"}),
            lambda p: p["actors"].update({"member": {"bot_id": "other-bot",
                                                     "owner_id": "e2e-density",
                                                     "dedicated": True}}),
            lambda p: p["actors"]["driver"].update({"dedicated": False}),
            lambda p: p["actors"]["driver"].update({"owner_id": "someone-else"}),
            lambda p: p["actors"]["driver"].update({"bot_id": ""}),
            lambda p: p["route_bindings"]["service_policy.settings_patch_retired"]
                .update({"method": "GET"}),
            lambda p: p["route_bindings"]["service_policy.ensure_human"]
                .update({"api_face": "public-v1"}),
            lambda p: p["route_bindings"].update({
                TEARDOWN_DERIVED_OPERATION: {"method": "PROJECTED",
                                             "path": "owned-local-teardown-projection",
                                             "api_face": "legacy"}}),
        )
        for mutate in mutations:
            profile = preprod_profile()
            mutate(profile)
            with self.assertRaises(RunStateError):
                validate_service_policy_density_profile(profile)
        profile = local_profile()
        profile["auth"]["cookie_env"] = "BCS_E2E_HUMAN_COOKIE"
        with self.assertRaises(RunStateError):
            validate_service_policy_density_profile(profile)


class ServicePolicyDensityRunTests(unittest.TestCase):
    def test_owned_local_replay_projects_the_full_offline_pass_receipt(self):
        profile = local_profile()
        report = run_service_policy_density(profile=profile)
        self.assertEqual(report["status"], STATUS_PASS)
        self.assertEqual(report["executor"], "owned-local-singlebox-replay")
        self.assertEqual(report["case_scope"]["parent_obligation_count"],
                         COARSE_PARENT_COUNT)
        self.assertEqual(report["case_scope"]["expected_obligation_count"],
                         PARSED_OBLIGATION_COUNT)
        self.assertEqual(report["case_scope"]["new_field_obligation_count"],
                         FIELD_OBLIGATION_COUNT)
        self.assertEqual(report["case_scope"]["delta"], FIELD_OBLIGATION_COUNT)
        self.assertEqual(report["execution"]["completed_count"], PARSED_OBLIGATION_COUNT)
        self.assertEqual(report["reports"]["pass_count"], PARSED_OBLIGATION_COUNT)
        self.assertEqual(report["reports"]["observed_obligation_count"],
                         PARSED_OBLIGATION_COUNT)
        self.assertEqual(len(report["assertions"]), PARSED_OBLIGATION_COUNT)
        self.assertTrue(all(row["status"] == STATUS_PASS
                            for row in report["assertions"]))
        for row in report["assertions"]:
            self.assertGreater(row["source"]["line"], 0)
            self.assertTrue(row["source"]["quote"])
            self.assertTrue(row["source"]["located"])
            self.assertEqual(row["source"]["file"], SOURCE_FILE)
        by_id = {row["obligation_id"]: row for row in report["assertions"]}
        f23_observed = by_id["SERVICE-POLICY.F23"]["observed"]
        self.assertEqual(sorted(set(f23_observed)),
                         sorted({INVOCATION_FIRST_SESSION_ID,
                                 INVOCATION_SECOND_SESSION_ID}))
        # the distinct comparator discloses the raw id accumulation across the
        # seven create-charged request sites (one parent plus six field rows)
        self.assertEqual(len(f23_observed), 7)
        self.assertEqual(by_id["SERVICE-POLICY.cleanup-disposition"]["observed"],
                         list(LOCAL_TEARDOWN_DISPOSITIONS))
        # status comparators charge the wire status only: the replay fixture's
        # disposable Group ids must not leak into the receipt bodies
        assertions_json = json.dumps(report["assertions"], ensure_ascii=False)
        self.assertNotIn(GROUP_OBJECT_ID, assertions_json)
        self.assertNotIn(GROUP_NULL_ID, assertions_json)
        self.assertIsNone(by_id["SERVICE-POLICY.F05"].get("observed"))
        self.assertEqual(report["remote_validation"], STATUS_NOT_RUN)
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(report["reconciliation"]["located_total"], PARSED_OBLIGATION_COUNT)

    def test_replay_field_failure_fails_fast_and_blocks_later_rows(self):
        profile = local_profile()
        observations = owned_local_replay_observations(profile)
        observations["service_policy.group_read_object"] = Observation(
            500, {"error": "replay injected transport failure"})
        report = run_service_policy_density(profile=profile,
                                            transport=ReplayTransport(observations))
        self.assertEqual(report["status"], STATUS_FAIL)
        self.assertEqual(report["execution"]["error_type"],
                         "SERVICE_POLICY_DENSITY_ASSERTION_FAILED")
        by_id = {row["obligation_id"]: row for row in report["assertions"]}
        self.assertEqual(by_id["SERVICE-POLICY.F04"]["status"], STATUS_FAIL)
        # status comparators own the wire status: the injected 500 discloses
        # as the observed status; the body is not charged
        self.assertEqual(by_id["SERVICE-POLICY.F04"]["observed"], 500)
        blocked = by_id["SERVICE-POLICY.F05"]
        self.assertEqual(blocked["status"], STATUS_NOT_RUN)
        self.assertEqual(blocked["error_type"], "BLOCKED_AFTER_ASSERTION_FAILURE")
        self.assertEqual(by_id["SERVICE-POLICY.mock-human-bootstrap"]["status"],
                         STATUS_PASS)
        self.assertEqual(report["reports"]["pass_count"],
                         COARSE_PARENT_COUNT + 3)

    def test_policy_absence_leak_fail_fast(self):
        profile = local_profile()
        observations = owned_local_replay_observations(profile)
        observations["service_policy.group_read_object"] = Observation(
            200, {"id": GROUP_OBJECT_ID, "service_spec": {"enabled": False}})
        report = run_service_policy_density(profile=profile,
                                            transport=ReplayTransport(observations))
        self.assertEqual(report["status"], STATUS_FAIL)
        by_id = {row["obligation_id"]: row for row in report["assertions"]}
        self.assertEqual(by_id["SERVICE-POLICY.F05"]["status"], STATUS_FAIL)
        self.assertIsNone(by_id["SERVICE-POLICY.F05"].get("observed"))
        self.assertEqual(by_id["SERVICE-POLICY.F06"]["status"], STATUS_NOT_RUN)

    def test_session_id_collapse_fail_fast(self):
        profile = local_profile()
        observations = owned_local_replay_observations(profile)
        observations["service_policy.invocation_create_second"] = Observation(
            202, {"session_id": INVOCATION_FIRST_SESSION_ID,
                  "session_kind": "service_invocation", "status": "running"})
        report = run_service_policy_density(profile=profile,
                                            transport=ReplayTransport(observations))
        self.assertEqual(report["status"], STATUS_FAIL)
        by_id = {row["obligation_id"]: row for row in report["assertions"]}
        self.assertEqual(by_id["SERVICE-POLICY.F23"]["status"], STATUS_FAIL)
        collapsed = by_id["SERVICE-POLICY.F23"]["observed"]
        self.assertEqual(set(collapsed), {INVOCATION_FIRST_SESSION_ID})
        self.assertGreaterEqual(len(collapsed), 2)

    def test_preprod_offline_fail_closes_to_blocked_inventoried_with_named_gap(self):
        report = run_service_policy_density(profile=preprod_profile())
        self.assertEqual(report["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(report["executor"], "preprod-bcs-direct-legacy")
        self.assertEqual(report["gap"]["id"], GAP_ID)
        self.assertEqual(report["gap"]["id"],
                         "SERVICE_POLICY_DENSITY_PREPROD_TRANSPORT_GAP")
        self.assertEqual(report["gap"]["kind"],
                         "PREPROD_TRANSPORT_NOT_ADMITTED_OFFLINE")
        self.assertIn("admitted", report["gap"]["detail"])
        self.assertIn("run seam", report["gap"]["resolution"])
        self.assertEqual(report["execution"]["status"], STATUS_NOT_RUN)
        self.assertEqual(report["execution"]["completed_count"], 0)
        self.assertEqual(len(report["assertions"]), PARSED_OBLIGATION_COUNT)
        for row in report["assertions"]:
            self.assertEqual(row["status"], STATUS_NOT_RUN)
            self.assertEqual(row["error_type"], "PREPROD_EXECUTION_NOT_RUN")
        self.assertEqual(report["remote_validation"], STATUS_NOT_RUN)
        self.assertFalse(report["grants_release_credit"])

    def test_injected_transport_seam_evaluates_both_tiers_identically(self):
        local = run_service_policy_density(profile=local_profile())
        self.assertEqual(local["status"], STATUS_PASS)
        observations = owned_local_replay_observations(preprod_profile())
        observations["service_policy.teardown_dispositions"] = Observation(
            200, {"dispositions": list(PREPROD_TEARDOWN_DISPOSITIONS)})
        preprod = run_service_policy_density(
            profile=preprod_profile(),
            transport=ReplayTransport(observations))
        self.assertEqual(preprod["status"], STATUS_PASS)
        self.assertEqual(preprod["executor"], "preprod-bcs-direct-legacy")
        self.assertEqual(preprod["execution"]["completed_count"], PARSED_OBLIGATION_COUNT)
        by_id = {row["obligation_id"]: row
                 for row in preprod["assertions"]}
        self.assertEqual(by_id["SERVICE-POLICY.cleanup-disposition"]["observed"],
                         list(PREPROD_TEARDOWN_DISPOSITIONS))
        self.assertIn(TEARDOWN_FENCE, PREPROD_TEARDOWN_DISPOSITIONS)
        self.assertNotIn(TEARDOWN_FENCE, LOCAL_TEARDOWN_DISPOSITIONS)

    def test_local_tier_rejects_the_preprod_fence_fixture(self):
        profile = local_profile()
        observations = owned_local_replay_observations(profile)
        observations["service_policy.teardown_dispositions"] = Observation(
            200, {"dispositions": list(PREPROD_TEARDOWN_DISPOSITIONS)})
        report = run_service_policy_density(profile=profile,
                                            transport=ReplayTransport(observations))
        self.assertEqual(report["status"], STATUS_FAIL)
        by_id = {row["obligation_id"]: row for row in report["assertions"]}
        self.assertEqual(by_id["SERVICE-POLICY.cleanup-disposition"]["status"],
                         STATUS_FAIL)
        self.assertEqual(by_id["SERVICE-POLICY.cleanup-disposition"]["observed"],
                         list(PREPROD_TEARDOWN_DISPOSITIONS))
        self.assertEqual(by_id["SERVICE-POLICY.F01"]["status"], STATUS_NOT_RUN)
        self.assertEqual(by_id["SERVICE-POLICY.F02"]["status"], STATUS_NOT_RUN)
        self.assertEqual(report["execution"]["completed_count"],
                         COARSE_PARENT_COUNT - 1)
        executed = sum(1 for row in report["assertions"]
                       if row["status"] != STATUS_NOT_RUN)
        self.assertEqual(executed, COARSE_PARENT_COUNT)
        self.assertEqual(len(report["assertions"]) - executed, FIELD_OBLIGATION_COUNT)
        self.assertEqual(report["reports"]["pass_count"], COARSE_PARENT_COUNT - 1)

    def test_seam_rejects_unknown_operations_and_propagates_transport_failures(self):
        profile = local_profile()
        empty = ReplayTransport({})
        report = run_service_policy_density(profile=profile, transport=empty)
        self.assertEqual(report["status"], STATUS_FAIL)
        by_id = {row["obligation_id"]: row for row in report["assertions"]}
        self.assertEqual(by_id["SERVICE-POLICY.mock-human-bootstrap"]["status"],
                         STATUS_FAIL)
        self.assertEqual(by_id["SERVICE-POLICY.mock-human-bootstrap"]["error_type"],
                         "RunStateError")
        self.assertEqual(by_id["SERVICE-POLICY.F01"]["status"], STATUS_NOT_RUN)

    def test_status_vocabulary_discriminates_pass_fail_and_blocked(self):
        passed = run_service_policy_density(profile=local_profile())
        observations = owned_local_replay_observations(local_profile())
        observations["service_policy.settings_patch_retired"] = Observation(
            200, {"error": "service_policy_retired"})
        failed = run_service_policy_density(profile=local_profile(),
                                            transport=ReplayTransport(observations))
        blocked = run_service_policy_density(profile=preprod_profile())
        self.assertEqual(passed["status"], STATUS_PASS)
        self.assertEqual(failed["status"], STATUS_FAIL)
        self.assertEqual(blocked["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual({row["status"] for row in passed["assertions"]},
                         {STATUS_PASS})
        self.assertEqual(failed["assertions"][3]["status"], STATUS_FAIL)
        self.assertEqual(failed["execution"]["completed_count"], 3)
        self.assertEqual(failed["execution"]["total_count"], PARSED_OBLIGATION_COUNT)
        self.assertEqual({row["status"] for row in blocked["assertions"]},
                         {STATUS_NOT_RUN})


class ServicePolicyDensityCaseRunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write_profile(self, name: str, profile: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(profile), encoding="utf-8")
        return path

    def test_confirm_and_write_gates_fail_closed_before_any_state_write(self):
        profile_path = self._write_profile("gate.json", local_profile())
        state_dir = self.root / "gate-state"
        with self.assertRaises(ConfigError):
            case_run(profile_path=profile_path, state_dir=state_dir,
                     run_id=LOCAL_RUN_ID, allow_write=False,
                     confirm_run_id=LOCAL_RUN_ID)
        with self.assertRaises(ConfigError):
            case_run(profile_path=profile_path, state_dir=state_dir,
                     run_id=LOCAL_RUN_ID, allow_write=True,
                     confirm_run_id="not-the-run-id")
        self.assertFalse(state_dir.exists())
        with self.assertRaises(ConfigError):
            case_run(profile_path=self.root / "missing.json", state_dir=state_dir,
                     run_id=LOCAL_RUN_ID, allow_write=True,
                     confirm_run_id=LOCAL_RUN_ID)
        garbage = self.root / "garbage.json"
        garbage.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ConfigError):
            case_run(profile_path=garbage, state_dir=state_dir,
                     run_id=LOCAL_RUN_ID, allow_write=True,
                     confirm_run_id=LOCAL_RUN_ID)
        local_placeholder = local_profile()
        local_placeholder["auth"]["driver_token_env"] = "REPLACE_WITH_DRIVER_TOKEN"
        with self.assertRaises(ConfigError):
            case_run(profile_path=self._write_profile("placeholder-local.json",
                                                      local_placeholder),
                     state_dir=self.root / "never-local", run_id=LOCAL_RUN_ID,
                     allow_write=True, confirm_run_id=LOCAL_RUN_ID)
        preprod_placeholder = preprod_profile()
        preprod_placeholder["auth"]["cookie_env"] = "REPLACE_WITH_HUMAN_COOKIE"
        with self.assertRaises(ConfigError):
            case_run(profile_path=self._write_profile("placeholder-preprod.json",
                                                      preprod_placeholder),
                     state_dir=self.root / "never-preprod", run_id=PREPROD_RUN_ID,
                     allow_write=True, confirm_run_id=PREPROD_RUN_ID)

    def test_local_case_run_writes_the_pass_receipt_triple(self):
        profile = local_profile()
        profile_path = self._write_profile("local.json", profile)
        state_dir = self.root / "local-state"
        report = case_run(profile_path=profile_path, state_dir=state_dir,
                          run_id=LOCAL_RUN_ID, allow_write=True,
                          confirm_run_id=LOCAL_RUN_ID)
        self.assertEqual(report["status"], STATUS_PASS)
        self.assertEqual(report["case_id"], CASE_ID)
        self.assertEqual(report["parent_id"], PARENT)
        self.assertEqual(report["command"], DUAL_SERVICE_POLICY_DENSITY_COMMAND)
        self.assertEqual(report["command"], "dual-service-policy-density-run")
        self.assertEqual(report["registration"], "REGISTERED_MAINLINE_SERIAL")
        self.assertEqual(report["contract"], REPORT_CONTRACT)
        self.assertEqual(report["credential_kind"], "owned_local_bot_token")
        self.assertEqual(report["resource_disposition"], RESOURCE_DISPOSITION)
        self.assertEqual(report["case_scope"]["parent_obligation_count"],
                         COARSE_PARENT_COUNT)
        self.assertEqual(report["case_scope"]["field_obligation_count"],
                         FIELD_OBLIGATION_COUNT)
        self.assertEqual(report["case_scope"]["expected_obligation_count"],
                         PARSED_OBLIGATION_COUNT)
        self.assertEqual(report["remote_validation"], STATUS_NOT_RUN)
        self.assertFalse(report["grants_release_credit"])
        self.assertNotIn("gap", report)
        self.assertEqual((state_dir / "service-policy-density-profile.json").exists(),
                         True)
        self.assertEqual((state_dir / "service-policy-density-binding.json").exists(),
                         True)
        self.assertEqual((state_dir / "service-policy-density-run.json").exists(), True)
        binding = json.loads(
            (state_dir / "service-policy-density-binding.json").read_text(
                encoding="utf-8"))
        self.assertEqual(binding["command"], DUAL_SERVICE_POLICY_DENSITY_COMMAND)
        self.assertEqual(binding["case_id"], CASE_ID)
        self.assertEqual(binding["parent_id"], PARENT)
        self.assertEqual(binding["run_id"], LOCAL_RUN_ID)
        self.assertTrue(binding["executor_digest"])
        self.assertEqual(binding["profile_digest"], report["profile_digest"])
        self.assertEqual(binding["registration"], "REGISTERED_MAINLINE_SERIAL")
        stored = json.loads(
            (state_dir / "service-policy-density-profile.json").read_text(
                encoding="utf-8"))
        self.assertEqual(stored, profile)
        run_receipt = json.loads(
            (state_dir / "service-policy-density-run.json").read_text(
                encoding="utf-8"))
        self.assertEqual(run_receipt["status"], STATUS_PASS)
        self.assertEqual(run_receipt["command"], DUAL_SERVICE_POLICY_DENSITY_COMMAND)
        self.assertEqual(run_receipt["execution"]["execution"]["completed_count"],
                         PARSED_OBLIGATION_COUNT)
        self.assertEqual((state_dir.stat().st_mode & 0o777), 0o700)
        with self.assertRaises(ConfigError):
            case_run(profile_path=profile_path, state_dir=state_dir,
                     run_id=LOCAL_RUN_ID, allow_write=True,
                     confirm_run_id=LOCAL_RUN_ID)

    def test_preprod_case_run_writes_the_named_gap_receipt(self):
        profile = preprod_profile()
        profile_path = self._write_profile("preprod.json", profile)
        state_dir = self.root / "preprod-state"
        report = case_run(profile_path=profile_path, state_dir=state_dir,
                          run_id=PREPROD_RUN_ID, allow_write=True,
                          confirm_run_id=PREPROD_RUN_ID)
        self.assertEqual(report["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(report["credential_kind"],
                         "human_cookie_with_owned_driver_bot_token")
        self.assertEqual(report["gap"]["id"], GAP_ID)
        self.assertEqual(report["environment"]["tier"], "preprod")
        self.assertEqual(report["environment"]["entrypoint_kind"], "bcs-direct")
        self.assertEqual(report["case_scope"]["expected_obligation_count"],
                         PARSED_OBLIGATION_COUNT)
        execution = report["execution"]
        self.assertEqual(execution["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(execution["execution"]["status"], STATUS_NOT_RUN)
        self.assertEqual(execution["execution"]["completed_count"], 0)
        not_run = sum(1 for row in execution["assertions"]
                      if row["status"] == STATUS_NOT_RUN)
        self.assertEqual(not_run, PARSED_OBLIGATION_COUNT)
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual((state_dir.stat().st_mode & 0o777), 0o700)

    def test_case_run_rejects_symlinked_or_relative_state_and_profile(self):
        profile = local_profile()
        profile_path = self._write_profile("ok.json", profile)
        with self.assertRaises(ConfigError):
            case_run(profile_path=Path("relative-profile.json"),
                     state_dir=self.root / "rel-state", run_id=LOCAL_RUN_ID,
                     allow_write=True, confirm_run_id=LOCAL_RUN_ID)
        existing = self.root / "existing-state"
        existing.mkdir()
        with self.assertRaises(ConfigError):
            case_run(profile_path=profile_path, state_dir=existing,
                     run_id=LOCAL_RUN_ID, allow_write=True,
                     confirm_run_id=LOCAL_RUN_ID)
        self.assertFalse((existing / "service-policy-density-run.json").exists())


if __name__ == "__main__":
    unittest.main()
