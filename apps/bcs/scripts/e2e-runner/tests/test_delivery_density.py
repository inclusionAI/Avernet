"""Focused offline tests for the delivery-policy field-level density slice.

Covers the source-anchored obligation builder (10 coarse parents aligned with
the committed migration contract plus 26 field obligations projected from 13
anchored upstream field sites), the strict profile admission for both tiers,
the owned-local offline PASS演算, the injected-transport failure fast-fail,
the preprod named-gap BLOCKED_INVENTORIED fail-close, and the unregistered
dual wrapper's confirmation gate and receipt triple.  No socket is opened and
no credential value is resolved anywhere in this module.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.delivery_density import (
    CASE_ID,
    COARSE_PARENT_COUNT,
    DUAL_DELIVERY_POLICY_DENSITY_COMMAND,
    FIELD_OBLIGATION_COUNT,
    FIELD_SITE_COUNT,
    NOT_CHARGED_SOURCE_PROBES,
    PARSED_OBLIGATION_COUNT,
    PARENT,
    STATUS_BLOCKED_INVENTORIED,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    Observation,
    ReplayTransport,
    build_delivery_density_obligations,
    delivery_density_credential_kind,
    delivery_density_plan,
    delivery_density_reconciliation,
    owned_local_replay_observations,
    owned_local_replay_transport,
    run_delivery_density,
    validate_delivery_density_profile,
)
from bcs_e2e.delivery_density.contract import (
    DENSITY_OPERATION_ROWS,
    REPORT_CONTRACT,
)
from bcs_e2e.delivery_density.reporting import (
    RESOURCE_DISPOSITION,
    dual_delivery_policy_density_case_run,
)
from bcs_e2e.delivery_density.run import GAP_ID
from bcs_e2e.local_delivery_policy_contract import REQUIREMENTS
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import RunStateError

STORIES_PATH = ROOT.parent / "e2e-test" / "stories.sh"
MIGRATION_CONTRACT_PATH = ROOT / "delivery-policy-migration-contract.json"
LOCAL_RUN_ID = "20261031-delivery-density-local"
PREPROD_RUN_ID = "20261031-delivery-density-preprod"


def local_profile() -> dict:
    return validate_delivery_density_profile({
        "schema_version": 1,
        "contract": "delivery-density-profile-v1",
        "environment": {"tier": "local", "id": "delivery-density-local",
                        "expected_bcs_env": "local"},
        "entrypoint": {"kind": "singlebox", "origin": "http://127.0.0.1:18080",
                       "api_face": "legacy", "manifest_face": "legacy"},
        "human": {"user_id": "e2e-density"},
        "actors": {"driver": {"bot_id": "delivery-density-driver", "owner_id": "e2e-density",
                              "dedicated": True},
                   "member": {"bot_id": "delivery-density-member", "owner_id": "e2e-density",
                              "dedicated": True}},
        "auth": {"kind": "owned-local-bot-tokens", "driver_token_env": "BCS_E2E_DRIVER_TOKEN"},
        "route_bindings": {row["operation"]: {"method": row["method"], "path": row["path"],
                                              "api_face": "legacy"}
                           for row in DENSITY_OPERATION_ROWS},
    })


def preprod_profile() -> dict:
    profile = local_profile()
    profile = json.loads(json.dumps(profile))
    profile["environment"] = {"tier": "preprod", "id": "bcs-pre-delivery-density",
                              "expected_bcs_env": "pre"}
    profile["entrypoint"] = {"kind": "bcs-direct", "origin": "https://bcs-pre.alipay.com",
                             "api_face": "legacy", "manifest_face": "legacy"}
    profile["auth"] = {"kind": "human-cookie-with-owned-bot-tokens",
                       "driver_token_env": "BCS_E2E_DRIVER_TOKEN",
                       "member_token_env": "BCS_E2E_MEMBER_TOKEN",
                       "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
    return validate_delivery_density_profile(profile)


class DeliveryDensityObligationTests(unittest.TestCase):
    def test_builder_projects_ten_parents_and_twenty_six_field_obligations(self):
        rows = build_delivery_density_obligations()
        self.assertEqual(len(rows), PARSED_OBLIGATION_COUNT)
        parents = [row for row in rows if row.level == "PARENT"]
        fields = [row for row in rows if row.level == "FIELD"]
        self.assertEqual(len(parents), COARSE_PARENT_COUNT)
        self.assertEqual(len(fields), FIELD_OBLIGATION_COUNT)
        self.assertEqual(FIELD_SITE_COUNT * 2, FIELD_OBLIGATION_COUNT)
        self.assertEqual([row.obligation_id for row in parents], list(REQUIREMENTS))
        parent_ids = {row.obligation_id for row in parents}
        self.assertTrue(all(row.parent_obligation in parent_ids for row in fields))
        for row in parents:
            self.assertFalse(row.density)
            self.assertIsNone(row.aspect)
            self.assertEqual(row.kind, "status")
            self.assertIsNone(row.field)
            self.assertEqual(row.path, "/admin/message-delivery/policy")
        for row in fields:
            self.assertTrue(row.density)
            self.assertEqual(row.kind, "field")
            self.assertIn(row.aspect, {"envelope", "value"})
            self.assertIn(row.field, {"version", "policy", "updated_by", "error"})
        self.assertEqual({row.family for row in rows},
                         {"READ", "DENIAL", "CAS", "CONFLICT", "RESTORE"})
        field_ids = [row.obligation_id for row in fields]
        self.assertEqual(len(set(field_ids)), FIELD_OBLIGATION_COUNT)
        self.assertTrue(all(fid.startswith("DELIVERY.F") for fid in field_ids))

    def test_every_obligation_anchors_to_a_live_stories_line(self):
        lines = STORIES_PATH.read_text(encoding="utf-8").splitlines()
        plan = delivery_density_plan()
        self.assertEqual(plan["parent"], PARENT)
        self.assertTrue(plan["source_read_only"])
        located = 0
        for row in plan["obligations"]:
            self.assertTrue(row["located"])
            self.assertEqual(row["source_file"], "apps/bcs/scripts/e2e-test/stories.sh")
            self.assertGreaterEqual(row["source_line"], 45)
            self.assertLessEqual(row["source_line"], 94)
            self.assertIn(row["source_quote"], lines[row["source_line"] - 1])
            self.assertIn(row["request_quote"], lines[row["request_line"] - 1])
            self.assertTrue(row["source_quote"])
            located += 1
        self.assertEqual(located, PARSED_OBLIGATION_COUNT)
        self.assertEqual(len(plan["not_charged_source_probes"]), len(NOT_CHARGED_SOURCE_PROBES))
        for probe in plan["not_charged_source_probes"]:
            self.assertIn(probe["source_quote"], lines[probe["source_line"] - 1])
            self.assertTrue(probe["reason"])

    def test_parents_align_with_the_migration_contract_and_coarse_family(self):
        contract = json.loads(MIGRATION_CONTRACT_PATH.read_text(encoding="utf-8"))
        by_id = {row["id"]: row for row in contract["requirements"]}
        self.assertEqual(len(by_id), COARSE_PARENT_COUNT)
        rows = {row.obligation_id: row for row in build_delivery_density_obligations()
                if row.level == "PARENT"}
        for requirement in REQUIREMENTS:
            row = rows[requirement]
            reviewed = by_id[requirement]
            self.assertEqual(row.expectation, reviewed["expectation"])
            self.assertEqual(row.method, reviewed["method"])
            self.assertEqual(row.path, reviewed["path"])
            self.assertEqual(row.caller, reviewed["actor"])
            self.assertEqual(row.operation,
                             "delivery_policy." + requirement.split(".", 1)[1].replace("-", "_"))
        self.assertEqual([row.obligation_id for row in build_delivery_density_obligations()
                          if row.level == "PARENT"], list(REQUIREMENTS))

    def test_reconciliation_discloses_every_parsed_caliber_honestly(self):
        reconciliation = delivery_density_reconciliation()
        self.assertEqual(reconciliation["coverage_map_snapshot_parsed_count"], 22)
        self.assertEqual(reconciliation["migration_queue_score"], 36)
        self.assertEqual(reconciliation["task_brief_parsed_caliber"], 36)
        self.assertEqual(reconciliation["raw_status_sites"], COARSE_PARENT_COUNT)
        self.assertEqual(reconciliation["raw_field_sites"], FIELD_SITE_COUNT)
        self.assertEqual(reconciliation["raw_anchored_sites"], COARSE_PARENT_COUNT + FIELD_SITE_COUNT)
        self.assertEqual(reconciliation["projected_parsed_total"], PARSED_OBLIGATION_COUNT)
        self.assertEqual(reconciliation["located_total"], PARSED_OBLIGATION_COUNT)
        self.assertEqual(reconciliation["parsed_unlocated_count"], 0)
        self.assertEqual(reconciliation["parsed_unlocated"], [])
        self.assertIn("guard", reconciliation["guard_line_53_note"])
        self.assertIn("story_human_manages_delivery_policy", reconciliation["story_id_note"])
        self.assertIn("story_user_manages_delivery_policy", reconciliation["story_id_note"])
        self.assertIn("envelope", reconciliation["projection_rule"])
        self.assertFalse(reconciliation["upstream_native_assertion_ids"])

    def test_builder_fail_closes_on_anchor_drift(self):
        rows = build_delivery_density_obligations()
        self.assertTrue(all(row.located for row in rows))
        corrupted = rows[0].source_quote
        self.assertIn(corrupted, STORIES_PATH.read_text(encoding="utf-8").splitlines()[rows[0].source_line - 1])


class DeliveryDensityProfileTests(unittest.TestCase):
    def test_admits_local_and_preprod_shapes_with_exact_key_sets(self):
        local = local_profile()
        preprod = preprod_profile()
        self.assertEqual(local["environment"]["expected_bcs_env"], "local")
        self.assertEqual(preprod["environment"]["expected_bcs_env"], "pre")
        self.assertEqual(preprod["entrypoint"]["kind"], "bcs-direct")
        self.assertEqual(preprod["entrypoint"]["api_face"], "legacy")
        self.assertEqual(local["auth"]["kind"], "owned-local-bot-tokens")
        self.assertEqual(preprod["auth"]["kind"], "human-cookie-with-owned-bot-tokens")
        self.assertEqual(delivery_density_credential_kind(local), "owned_local_bot_token")
        self.assertEqual(delivery_density_credential_kind(preprod),
                         "human_cookie_with_owned_driver_bot_token")
        self.assertEqual(set(DENSITY_OPERATION_ROWS[0]),
                         {"requirement_id", "operation", "method", "path", "caller"})

    def test_rejects_schema_contract_and_key_set_drift(self):
        for mutate in (
            lambda p: p.update({"schema_version": 2}),
            lambda p: p.update({"contract": "delivery-density-profile-v2"}),
            lambda p: p.update({"secrets": {"note": "inline-value-forbidden"}}),
            lambda p: p["environment"].update({"extra": "x"}),
            lambda p: p["route_bindings"].update({"delivery_policy.unknown_op": {
                "method": "GET", "path": "/admin/message-delivery/policy", "api_face": "legacy"}}),
        ):
            profile = local_profile()
            mutate(profile)
            with self.assertRaises(RunStateError):
                validate_delivery_density_profile(profile)
        profile = local_profile()
        profile["route_bindings"].pop("delivery_policy.human_read")
        with self.assertRaises(RunStateError):
            validate_delivery_density_profile(profile)

    def test_rejects_tier_entrypoint_and_face_drift(self):
        mutations = (
            lambda p: p["entrypoint"].update({"kind": "gateway"}),
            lambda p: p["entrypoint"].update({"origin": "https://127.0.0.1:18080"}),
            lambda p: p["entrypoint"].update({"origin": "http://127.0.0.1"}),
            lambda p: p["entrypoint"].update({"api_face": "public-v1"}),
            lambda p: p["entrypoint"].update({"manifest_face": "internal-v1"}),
            lambda p: p["environment"].update({"tier": "station"}),
            lambda p: p["environment"].update({"expected_bcs_env": "pre"}),
        )
        for mutate in mutations:
            profile = local_profile()
            mutate(profile)
            with self.assertRaises(RunStateError):
                validate_delivery_density_profile(profile)
        for mutate in (
            lambda p: p["entrypoint"].update({"kind": "singlebox"}),
            lambda p: p["entrypoint"].update({"origin": "http://bcs-pre.alipay.com"}),
            lambda p: p["environment"].update({"expected_bcs_env": "local"}),
            lambda p: p["auth"].update({"kind": "owned-local-bot-tokens"}),
        ):
            profile = preprod_profile()
            mutate(profile)
            with self.assertRaises(RunStateError):
                validate_delivery_density_profile(profile)

    def test_rejects_credential_value_posture_and_identity_drift(self):
        mutations = (
            lambda p: p["auth"].update({"driver_token_env": "bcs_e2e_driver_token"}),
            lambda p: p["auth"].update({"driver_token_env": "BC"}),
            lambda p: p["auth"].update({"cookie_env": "bcs_e2e_human_cookie"}),
            lambda p: p["auth"].pop("member_token_env"),
            lambda p: p["actors"]["driver"].update({"dedicated": False}),
            lambda p: p["actors"]["driver"].update({"owner_id": "someone-else"}),
            lambda p: p["actors"]["member"].update({"bot_id": "delivery-density-driver"}),
            lambda p: p["route_bindings"]["delivery_policy.cas_update"].update({"method": "GET"}),
            lambda p: p["route_bindings"]["delivery_policy.human_read"].update({"api_face": "public-v1"}),
        )
        for mutate in mutations:
            profile = preprod_profile()
            mutate(profile)
            with self.assertRaises(RunStateError):
                validate_delivery_density_profile(profile)
        profile = local_profile()
        profile["auth"]["driver_token_env"] = "BCS_E2E_DRIVER_TOKEN"
        profile["auth"]["cookie_env"] = "BCS_E2E_HUMAN_COOKIE"
        with self.assertRaises(RunStateError):
            validate_delivery_density_profile(profile)


class DeliveryDensityRunTests(unittest.TestCase):
    def test_owned_local_replay_projects_a_full_offline_pass_receipt(self):
        profile = local_profile()
        report = run_delivery_density(profile=profile)
        self.assertEqual(report["status"], STATUS_PASS)
        self.assertEqual(report["executor"], "owned-local-singlebox-replay")
        self.assertEqual(report["case_scope"]["expected_obligation_count"], PARSED_OBLIGATION_COUNT)
        self.assertEqual(report["case_scope"]["new_field_obligation_count"], FIELD_OBLIGATION_COUNT)
        self.assertEqual(report["execution"]["completed_count"], PARSED_OBLIGATION_COUNT)
        self.assertEqual(report["reports"]["pass_count"], PARSED_OBLIGATION_COUNT)
        self.assertTrue(all(row["status"] == STATUS_PASS for row in report["assertions"]))
        for row in report["assertions"]:
            self.assertGreater(row["source"]["line"], 0)
            self.assertTrue(row["source"]["quote"])
            self.assertTrue(row["source"]["located"])
        self.assertEqual(report["remote_validation"], STATUS_NOT_RUN)
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(report["reconciliation"]["located_total"], PARSED_OBLIGATION_COUNT)
        self.assertEqual(len(report["assertions"]), PARSED_OBLIGATION_COUNT)

    def test_replay_failure_fails_fast_and_blocks_later_rows(self):
        profile = local_profile()
        observations = owned_local_replay_observations(profile)
        observations["delivery_policy.cas_update"] = Observation(
            200, {"version": 7, "policy": {"max_context_messages": 23},
                  "updated_by": "human_e2e-density", "updated_at_ms": 1698765432000})
        report = run_delivery_density(profile=profile,
                                      transport=ReplayTransport(observations))
        self.assertEqual(report["status"], STATUS_FAIL)
        by_id = {row["obligation_id"]: row for row in report["assertions"]}
        self.assertEqual(by_id["DELIVERY.cas-update"]["status"], STATUS_PASS)
        self.assertEqual(by_id["DELIVERY.F04.envelope"]["status"], STATUS_PASS)
        self.assertEqual(by_id["DELIVERY.F04.value"]["status"], STATUS_FAIL)
        self.assertEqual(by_id["DELIVERY.F04.value"]["observed"], 7)
        blocked = by_id["DELIVERY.F05.envelope"]
        self.assertEqual(blocked["status"], STATUS_NOT_RUN)
        self.assertEqual(blocked["error_type"], "BLOCKED_AFTER_ASSERTION_FAILURE")
        self.assertLess(report["execution"]["completed_count"], PARSED_OBLIGATION_COUNT)

    def test_preprod_offline_fail_closes_to_blocked_inventoried_with_named_gap(self):
        report = run_delivery_density(profile=preprod_profile())
        self.assertEqual(report["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(report["executor"], "preprod-bcs-direct-legacy")
        self.assertEqual(report["gap"]["id"], GAP_ID)
        self.assertEqual(report["gap"]["id"], "DELIVERY_DENSITY_PREPROD_TRANSPORT_GAP")
        self.assertTrue(report["gap"]["detail"])
        self.assertEqual(report["execution"]["status"], STATUS_NOT_RUN)
        self.assertEqual(report["execution"]["completed_count"], 0)
        self.assertEqual(len(report["assertions"]), PARSED_OBLIGATION_COUNT)
        for row in report["assertions"]:
            self.assertEqual(row["status"], STATUS_NOT_RUN)
            self.assertEqual(row["error_type"], "PREPROD_EXECUTION_NOT_RUN")
        self.assertEqual(report["remote_validation"], STATUS_NOT_RUN)
        self.assertFalse(report["grants_release_credit"])

    def test_injected_transport_seam_evaluates_both_tiers_identically(self):
        local = run_delivery_density(profile=local_profile(),
                                     transport=owned_local_replay_transport(local_profile()))
        self.assertEqual(local["status"], STATUS_PASS)
        preprod = run_delivery_density(profile=preprod_profile(),
                                       transport=owned_local_replay_transport(preprod_profile()))
        self.assertEqual(preprod["status"], STATUS_PASS)
        self.assertEqual(preprod["executor"], "preprod-bcs-direct-legacy")

    def test_status_vocabulary_discriminates_pass_fail_and_blocked(self):
        passed = run_delivery_density(profile=local_profile())
        self.assertEqual(passed["status"], STATUS_PASS)
        observations = owned_local_replay_observations(local_profile())
        observations["delivery_policy.stale_rejected"] = Observation(
            200, {"version": 8, "policy": {"max_context_messages": 23},
                  "updated_by": "human_e2e-density", "updated_at_ms": 1698765432000})
        failed = run_delivery_density(profile=local_profile(),
                                      transport=ReplayTransport(observations))
        self.assertEqual(failed["status"], STATUS_FAIL)
        self.assertEqual(failed["execution"]["error_type"], "DELIVERY_DENSITY_ASSERTION_FAILED")
        blocked = run_delivery_density(profile=preprod_profile())
        self.assertEqual(blocked["status"], STATUS_BLOCKED_INVENTORIED)
        statuses = {row["status"] for row in passed["assertions"]}
        self.assertEqual(statuses, {STATUS_PASS})
        self.assertIn(failed["assertions"][0]["status"], {STATUS_PASS, STATUS_FAIL})


class DeliveryDensityCaseRunTests(unittest.TestCase):
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
            dual_delivery_policy_density_case_run(
                profile_path=profile_path, state_dir=state_dir, run_id=LOCAL_RUN_ID,
                allow_write=False, confirm_run_id=LOCAL_RUN_ID)
        with self.assertRaises(ConfigError):
            dual_delivery_policy_density_case_run(
                profile_path=profile_path, state_dir=state_dir, run_id=LOCAL_RUN_ID,
                allow_write=True, confirm_run_id="not-the-run-id")
        self.assertFalse(state_dir.exists())
        with self.assertRaises(ConfigError):
            dual_delivery_policy_density_case_run(
                profile_path=self.root / "missing.json", state_dir=state_dir,
                run_id=LOCAL_RUN_ID, allow_write=True, confirm_run_id=LOCAL_RUN_ID)
        garbage = self.root / "garbage.json"
        garbage.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ConfigError):
            dual_delivery_policy_density_case_run(
                profile_path=garbage, state_dir=state_dir, run_id=LOCAL_RUN_ID,
                allow_write=True, confirm_run_id=LOCAL_RUN_ID)
        placeholder = local_profile()
        placeholder["auth"]["driver_token_env"] = "REPLACE_WITH_DRIVER_TOKEN"
        with self.assertRaises(ConfigError):
            dual_delivery_policy_density_case_run(
                profile_path=self._write_profile("placeholder.json", placeholder),
                state_dir=self.root / "never", run_id=LOCAL_RUN_ID,
                allow_write=True, confirm_run_id=LOCAL_RUN_ID)

    def test_local_case_run_writes_the_pass_receipt_triple(self):
        profile = local_profile()
        profile_path = self._write_profile("local.json", profile)
        state_dir = self.root / "local-state"
        report = dual_delivery_policy_density_case_run(
            profile_path=profile_path, state_dir=state_dir, run_id=LOCAL_RUN_ID,
            allow_write=True, confirm_run_id=LOCAL_RUN_ID)
        self.assertEqual(report["status"], STATUS_PASS)
        self.assertEqual(report["case_id"], CASE_ID)
        self.assertEqual(report["parent_id"], PARENT)
        self.assertEqual(report["command"], DUAL_DELIVERY_POLICY_DENSITY_COMMAND)
        self.assertEqual(report["command"], "dual-delivery-policy-density-run")
        self.assertEqual(report["registration"], "REGISTERED_MAINLINE_SERIAL")
        self.assertEqual(report["contract"], REPORT_CONTRACT)
        self.assertEqual(report["credential_kind"], "owned_local_bot_token")
        self.assertEqual(report["resource_disposition"], RESOURCE_DISPOSITION)
        self.assertEqual(report["case_scope"]["parent_obligation_count"], COARSE_PARENT_COUNT)
        self.assertEqual(report["case_scope"]["expected_obligation_count"], PARSED_OBLIGATION_COUNT)
        self.assertEqual(report["remote_validation"], STATUS_NOT_RUN)
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual((state_dir / "delivery-policy-density-profile.json").exists(), True)
        self.assertEqual((state_dir / "delivery-policy-density-binding.json").exists(), True)
        self.assertEqual((state_dir / "delivery-policy-density-run.json").exists(), True)
        binding = json.loads((state_dir / "delivery-policy-density-binding.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["command"], DUAL_DELIVERY_POLICY_DENSITY_COMMAND)
        self.assertEqual(binding["case_id"], CASE_ID)
        self.assertTrue(binding["executor_digest"])
        self.assertEqual(binding["profile_digest"], report["profile_digest"])
        stored = json.loads((state_dir / "delivery-policy-density-profile.json").read_text(encoding="utf-8"))
        self.assertEqual(stored, profile)
        run_receipt = json.loads((state_dir / "delivery-policy-density-run.json").read_text(encoding="utf-8"))
        self.assertEqual(run_receipt["status"], STATUS_PASS)
        self.assertEqual(run_receipt["execution"]["execution"]["completed_count"], PARSED_OBLIGATION_COUNT)
        self.assertEqual((state_dir.stat().st_mode & 0o777), 0o700)
        with self.assertRaises(ConfigError):
            dual_delivery_policy_density_case_run(
                profile_path=profile_path, state_dir=state_dir, run_id=LOCAL_RUN_ID,
                allow_write=True, confirm_run_id=LOCAL_RUN_ID)

    def test_preprod_case_run_writes_the_named_gap_receipt(self):
        profile = preprod_profile()
        profile_path = self._write_profile("preprod.json", profile)
        state_dir = self.root / "preprod-state"
        report = dual_delivery_policy_density_case_run(
            profile_path=profile_path, state_dir=state_dir, run_id=PREPROD_RUN_ID,
            allow_write=True, confirm_run_id=PREPROD_RUN_ID)
        self.assertEqual(report["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(report["credential_kind"], "human_cookie_with_owned_driver_bot_token")
        self.assertEqual(report["gap"]["id"], GAP_ID)
        self.assertEqual(report["environment"]["tier"], "preprod")
        self.assertEqual(report["environment"]["entrypoint_kind"], "bcs-direct")
        execution = report["execution"]
        self.assertEqual(execution["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(execution["execution"]["status"], STATUS_NOT_RUN)
        self.assertEqual(execution["execution"]["completed_count"], 0)
        not_run = sum(1 for row in execution["assertions"] if row["status"] == STATUS_NOT_RUN)
        self.assertEqual(not_run, PARSED_OBLIGATION_COUNT)
        self.assertFalse(report["grants_release_credit"])


if __name__ == "__main__":
    unittest.main()
