"""Focused offline guards for the dual Edge-permission backlog cases."""
from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bcs_e2e.edge_permission_backlog.case import (
    CASE_IDS,
    CASE_KEYS,
    PARENT_IDS,
    edge_permission_backlog_case_contract,
    dual_case_adaptability,
)
from bcs_e2e.edge_permission_backlog.reporting import case_summary
from bcs_e2e.edge_permission_backlog.case_run import (
    dual_edge_permission_backlog_case_run,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read

RUN_ID = "edge-permission-backlog-1"

EXPECTED_COUNTS = {
    "tc-friend-auth-sync": 2,
    "dept-scope-friend-ext": 4,
}


class EdgePermissionBacklogCaseTests(unittest.TestCase):
    def test_both_backlog_cases_expose_exact_parsed_density(self):
        for case_key, expected in EXPECTED_COUNTS.items():
            with self.subTest(case_key=case_key):
                case = edge_permission_backlog_case_contract(case_key)
                self.assertEqual(case.required_assertion_count, expected)
                self.assertEqual(len(case.requirement_ids), expected)
                self.assertIn(case.case_id, CASE_IDS)
                self.assertIn(case.parent_id, PARENT_IDS)
                self.assertEqual(case.case_version, 1)
                self.assertEqual(case.environment, "LOCAL_MODEL_READY")

    def test_dual_adaptability_and_reporting_stay_offline(self):
        adaptability = dual_case_adaptability()
        self.assertEqual(
            adaptability["totals"]["required_assertion_count"], 6)
        self.assertEqual(adaptability["environments"]["preprod"], "NOT_RUN")
        self.assertFalse(adaptability["grants_release_credit"])
        summary = case_summary()
        self.assertEqual(summary["required_assertion_count"], 6)
        self.assertEqual(summary["status"], "BLOCKED_INVENTORY")
        self.assertEqual(summary["remote_validation"], "NOT_RUN")


class EdgePermissionBacklogCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def _profile(self, case_key: str, tier: str = "local") -> Path:
        entrypoint = "singlebox" if tier == "local" else "gateway"
        payload = {
            "schema_version": 1,
            "contract": "dual-edge-permission-backlog-profile-v1",
            "environment": {
                "tier": tier,
                "id": "local" if tier == "local" else "pre",
                "entrypoint": entrypoint,
            },
            "policy": {"timeout_seconds": 30, "maximum_response_bytes": 1048576},
        }
        path = self.root / f"profile-{tier}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def invoke(self, case_key: str, tier: str = "local"):
        state_dir = self.root / f"state-{case_key}-{tier}"
        return dual_edge_permission_backlog_case_run(
            profile_path=self._profile(case_key, tier),
            case_key=case_key,
            state_dir=state_dir,
            run_id=RUN_ID,
            confirm_run_id=RUN_ID,
            allow_write=True,
        ), state_dir

    def test_local_run_writes_three_file_dual_receipt(self):
        report, state_dir = self.invoke("tc-friend-auth-sync")
        self.assertEqual(report["case_id"], "LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01")
        self.assertEqual(report["parent_id"], "test_ep_tc_friend_auth_sync_readdressed_principal")
        self.assertEqual(report["required_assertion_count"], 2)
        self.assertEqual(report["local_status"], "LOCAL_MODEL_READY")
        self.assertEqual(report["preprod_status"], "NOT_RUN")
        self.assertEqual(report["status"], "BLOCKED_INVENTORY")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        binding = read(state_dir / "edge-permission-backlog-binding.json")
        self.assertEqual(binding["case_key"], "tc-friend-auth-sync")
        self.assertEqual(binding["requirement_ids"], report["requirement_ids"])
        self.assertEqual(read(state_dir / "edge-permission-backlog-run.json"), report)
        self.assertEqual(stat.S_IMODE(state_dir.stat().st_mode), 0o700)

    def test_preprod_run_keeps_remote_not_run_and_counts_four(self):
        report, state_dir = self.invoke("dept-scope-friend-ext", tier="preprod")
        self.assertEqual(report["case_id"], "LOCAL-EDGE-DEPT-SCOPE-FRIEND-EXT-01")
        self.assertEqual(report["parent_id"], "test_ep_dept_scope_parses_object_friend_ext_entries")
        self.assertEqual(report["required_assertion_count"], 4)
        self.assertEqual(report["environment"]["tier"], "preprod")
        self.assertEqual(report["preprod_status"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(read(state_dir / "edge-permission-backlog-run.json"), report)

    def test_permission_confirmation_and_fresh_state_are_enforced(self):
        state_dir = self.root / "state"
        with self.assertRaisesRegex(ConfigError, "explicit write permission"):
            dual_edge_permission_backlog_case_run(
                profile_path=self._profile("tc-friend-auth-sync"),
                case_key="tc-friend-auth-sync",
                state_dir=state_dir,
                run_id=RUN_ID,
                confirm_run_id=RUN_ID,
                allow_write=False)
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_edge_permission_backlog_case_run(
                profile_path=self._profile("tc-friend-auth-sync"),
                case_key="tc-friend-auth-sync",
                state_dir=state_dir,
                run_id=RUN_ID,
                confirm_run_id="other",
                allow_write=True)
        state_dir.mkdir(mode=0o700)
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_edge_permission_backlog_case_run(
                profile_path=self._profile("tc-friend-auth-sync"),
                case_key="tc-friend-auth-sync",
                state_dir=state_dir,
                run_id=RUN_ID,
                confirm_run_id=RUN_ID,
                allow_write=True)
        self.assertEqual(list(state_dir.iterdir()), [])

    def test_profile_rejects_unknown_case_and_tier_drift(self):
        path = self._profile("tc-friend-auth-sync")
        raw = json.loads(path.read_text())
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "one of the two reviewed slices"):
            dual_edge_permission_backlog_case_run(
                profile_path=path,
                case_key="unknown",
                state_dir=self.root / "state",
                run_id=RUN_ID,
                confirm_run_id=RUN_ID,
                allow_write=True)

        path = self._profile("tc-friend-auth-sync", tier="preprod")
        raw = json.loads(path.read_text())
        raw["environment"]["tier"] = "local"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "environment is invalid"):
            dual_edge_permission_backlog_case_run(
                profile_path=path,
                case_key="tc-friend-auth-sync",
                state_dir=self.root / "state-2",
                run_id=RUN_ID,
                confirm_run_id=RUN_ID,
                allow_write=True)

    def test_all_case_keys_map_to_exact_obligation_families(self):
        self.assertEqual(CASE_KEYS, tuple(EXPECTED_COUNTS))


if __name__ == "__main__":
    unittest.main()
