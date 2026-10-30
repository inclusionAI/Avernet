"""Focused offline guards for executable red Edge dual registration (#40/#41)."""
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bcs_e2e.case_command_registration import register_case_commands
from bcs_e2e.inventory import legacy_inventory_with_executor_migration
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.route_bindings import _EDGE_CASES
from bcs_e2e.run_state import RunStateError
from bcs_e2e.edge_friend_mutation_case import BACKLOG_CASE_IDS, CASE_IDS
from bcs_e2e.local_edge_backlog import (
    DEPT_REMOTE_PUBLIC_PROFILE,
    TC_REMOTE_PUBLIC_PROFILE,
    load_dept_contract,
    load_tc_contract,
)


class EdgeRedEdgeDualRegistrationTests(unittest.TestCase):
    """Registration, schema, local selection and route-source guards."""

    def test_registration_equivalence_points_to_the_executable_dual_command(self):
        rows = migration_plan()["local_backlog_case_slices"]
        by_case = {
            row["case_id"]: row
            for row in rows
            if row["case_id"] in set(BACKLOG_CASE_IDS)
        }
        self.assertEqual(set(by_case), set(BACKLOG_CASE_IDS))
        for row in by_case.values():
            with self.subTest(case_id=row["case_id"]):
                self.assertEqual(row["status"], "DUAL_EXECUTION_READY")
                self.assertEqual(row["canonical_reporting"], "DUAL_EXECUTION_READY")
                self.assertEqual(row["execution_command"], "dual-edge-friend-mutation-run")
                self.assertEqual(row["required_parameters"], [
                    "profile", "station_profile", "case", "state_dir",
                    "run_id", "confirm_run_id", "allow_write",
                ])
                self.assertEqual(row["remote_validation"], "NOT_RUN")
                self.assertFalse(row["grants_release_credit"])

        statuses = legacy_inventory_with_executor_migration()
        by_legacy = {
            row["id"]: row["executor_migration"]
            for row in statuses
            if row["id"] in {
                "test_ep_tc_friend_auth_sync_readdressed_principal",
                "test_ep_dept_scope_parses_object_friend_ext_entries",
            }
        }
        for status in by_legacy.values():
            with self.subTest(status=status):
                self.assertEqual(status["mode"], "DUAL")
                self.assertEqual(status["preprod_verdict"], "NOT_RUN")
                self.assertEqual(status["gates"], [])
                self.assertEqual(status["notes"], [])

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        register_case_commands(subparsers)
        for case_key in ("tc-friend-auth-sync", "dept-scope-friend-ext"):
            with self.subTest(case_key=case_key):
                args = parser.parse_args([
                    "dual-edge-friend-mutation-run", "--profile", "/gateway.json",
                    "--station-profile", "/station.json", "--case", case_key,
                    "--state-dir", "/state", "--run-id", "run-1",
                    "--confirm-run-id", "run-1", "--allow-write",
                ])
                self.assertEqual(args.command, "dual-edge-friend-mutation-run")
                self.assertEqual(args.case, case_key)
                self.assertTrue(args.allow_write)

    def test_migration_contract_schema_and_remote_profiles_are_route_exact(self):
        tc, _ = load_tc_contract()
        dept, _ = load_dept_contract()
        for payload in (tc, dept):
            with self.subTest(contract=payload["case_id"]):
                self.assertEqual(payload["remote_validation"], "NOT_RUN")
                self.assertFalse(payload["grants_release_credit"])
                self.assertFalse(payload["release_parent_denominator"])
                self.assertEqual(payload["environment"]["remote"]["entrypoint_kind"],
                                 "preprod-public-v1")

        tc_remote = tc["route_profiles"]["remote-public"]["routes"]
        dept_remote = dept["route_profiles"]["remote-public"]["routes"]
        self.assertEqual(
            tc_remote["create"]["path"],
            "/openapi/v1/collaboration/friend-connections/requests")
        self.assertEqual(tc_remote["create"]["success_statuses"], [201])
        self.assertEqual(
            tc_remote["accept"]["path"],
            "/openapi/v1/collaboration/friend-connections/requests/{request_id}/accept")
        for name, route in TC_REMOTE_PUBLIC_PROFILE.routes.items():
            binding = tc_remote[name]
            self.assertEqual(binding["method"], route.method)
            self.assertEqual(binding["path"], route.path)
            self.assertEqual(binding["success_statuses"], list(route.success_statuses))
            self.assertEqual(binding["actor"], route.actor)
            self.assertEqual(
                binding.get("availability", ""),
                route.availability,
            )
        self.assertEqual(dept_remote["mixed_patch"]["path"],
                         "/openapi/v1/collaboration/bots/{bot_id}")
        self.assertEqual(dept_remote["no_check_create"]["success_statuses"], [201])
        self.assertEqual(dept_remote["human_scoped_create"]["success_statuses"], [201, 403])
        self.assertEqual(dept_remote["bot_scoped_create"]["success_statuses"], [201, 403])

    def test_preprod_adapter_source_uses_exact_credential_names_and_routes(self):
        source = (ROOT / "bcs_e2e" / "remote_edge_friend_mutation_adapter.py").read_text(
            encoding="utf-8")
        self.assertIn('HUMAN_COOKIE_ENV = "BCS_E2E_HUMAN_COOKIE"', source)
        self.assertIn('STATION_BOT_TOKEN_ENV = "BCS_E2E_STATION_BOT_TOKEN"', source)
        for method in (
            "run_tc_friend_request_create_accept",
            "run_tc_friend_auth_sync_readdress",
            "run_dept_scope_mixed_owner_patch",
            "run_dept_scope_human_no_check",
            "run_dept_scope_human_user_scope",
            "run_dept_scope_bot_agent_scope",
        ):
            self.assertIn("def " + method + "(", source)
        for method in (
            "run_tc_friend_request_create_accept",
            "run_dept_scope_mixed_owner_patch",
            "run_dept_scope_human_no_check",
            "run_dept_scope_human_user_scope",
            "run_dept_scope_bot_agent_scope",
        ):
            self.assertNotIn('raise self._service_gap("' + method + '")', source)
        self.assertIn("friend_connection_mutation.create", source)
        self.assertIn("friend_connection_mutation.create_as_bot", source)
        self.assertIn("friend_connection_mutation.accept", source)
        self.assertIn("friend_connection_mutation.visibility.write", source)
        self.assertIn('"auth": "human_cookie_owned_bot"', source)
        self.assertEqual(_EDGE_CASES["friend_connection_mutation.create_as_bot"], (
            "POST",
            "/openapi/v1/collaboration/friend-connections/requests",
            "human_cookie_owned_bot",
        ))

    def test_station_rust_router_mounts_are_the_only_route_truth(self):
        repo = ROOT.parents[3]
        openapi_router = (
            repo / "apps/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/mod.rs"
        ).read_text(encoding="utf-8")
        friend_router = (
            repo / "apps/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/friend_connection.rs"
        ).read_text(encoding="utf-8")
        bot_router = (
            repo / "apps/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/bot.rs"
        ).read_text(encoding="utf-8")
        self.assertIn('"/openapi/v1/collaboration"', openapi_router)
        self.assertIn("routes::friend_connection::router()", openapi_router)
        self.assertIn('"/friend-connections/requests"', friend_router)
        self.assertIn("post(create_friend_connection_request)", friend_router)
        self.assertIn('"/friend-connections/requests/{request_id}/accept"', friend_router)
        self.assertIn('"/bots/{bot_id}"', bot_router)
        self.assertIn("patch(update_bot)", bot_router)

    def test_local_executor_supports_exact_ordered_red_case_selection_only(self):
        from bcs_e2e.local_edge_friend_mutations_run import local_edge_friend_mutations_run

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "fresh"
            for selection in (
                ("unknown-case",),
                ("LOCAL-EDGE-DEPT-SCOPE-FRIEND-EXT-01",
                 "LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01"),
                ("LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01",
                 "LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01"),
            ):
                with self.subTest(selection=selection):
                    with self.assertRaisesRegex(
                            RunStateError, "non-empty ordered subsequence"):
                        local_edge_friend_mutations_run(
                            binary=state, binary_sha256="not-a-binary",
                            state_dir=state, run_id="offline-guard",
                            confirm_run_id="offline-guard", allow_local_processes=False,
                            allow_fixture_write=False, allow_write=False,
                            case_ids=selection)
            self.assertFalse(state.exists())

    def test_local_runtime_selection_covers_both_red_cases_and_stays_offline(self):
        # The two reviewed red cases together form the complete ordered backlog
        # subsequence.  This test proves the selection set without starting the
        # owned BCS binary or any local process.
        selected = tuple(value for value in CASE_IDS if value in set(BACKLOG_CASE_IDS))
        self.assertEqual(selected, BACKLOG_CASE_IDS)
        self.assertEqual(selected, ("LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01",
                                   "LOCAL-EDGE-DEPT-SCOPE-FRIEND-EXT-01"))


if __name__ == "__main__":
    unittest.main()
