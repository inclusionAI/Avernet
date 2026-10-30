"""Regression guards for the blocked-case error_detail lens.

The dual-environment runner carries the first failing exception text (capped
at 800 characters) on the blocked CaseExecution and its failing step, and the
session-file / friend envelopes forward it, so remote BLOCKED verdicts arrive
with their root cause instead of a bare exception class name.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TEST_ROOT = Path(__file__).resolve().parent
ROOT = TEST_ROOT.parent
RUNNER = ROOT / "bcs_e2e"
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))

from bcs_e2e.case_executor import CaseExecution, StepOutcome, record_error_detail
from bcs_e2e.run_state import RunStateError
import test_workspace_group_lifecycle as ws
import test_remote_workspace_cli_create_get_adapter as cg
from bcs_e2e.workspace_cli_create_get_contract import (
    RemoteWorkspaceCliCreateGetAdapter,
    execute_workspace_cli_create_get_case,
)

DETAIL = "network probe message for diagnostics"

FORWARDING_FILES = [
    "session_file_core_case_run.py",
    "friend_connection_decision_case_run.py",
    "friend_public_accept_case_run.py",
    "edge_friend_mutation_case_run.py",
    "custom_collaboration_cli_case_run.py",
    "collaboration_team_case_run.py",
]


class RecordErrorDetailTests(unittest.TestCase):
    def test_short_message_passthrough(self):
        self.assertEqual(record_error_detail(RunStateError(DETAIL)), DETAIL)

    def test_empty_message_is_none(self):
        self.assertIsNone(record_error_detail(RuntimeError("")))

    def test_boundary_eight_hundred_is_kept(self):
        message = "x" * 800
        self.assertEqual(record_error_detail(ValueError(message)), message)

    def test_over_eight_hundred_is_capped(self):
        message = "y" * 1200
        value = record_error_detail(ValueError(message))
        self.assertEqual(len(value), 800)
        self.assertTrue(value.endswith("..."))
        self.assertEqual(value[:-3], "y" * 797)


class SnapshotForwardingTests(unittest.TestCase):
    def test_case_execution_snapshot_carries_detail(self):
        execution = CaseExecution(
            case_id="LOCAL-LENS-01", parent_id="PARENT", status="BLOCKED",
            steps=(StepOutcome(requirement_id="R1", method="m",
                               status="NOT_RUN", data=None, evidence={},
                               error_type="RunStateError",
                               error_detail=DETAIL),),
            completed_count=0, total_count=1, error_type="RunStateError",
            ready_to_execute=False, error_detail=DETAIL)
        execution.validate()
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["error_detail"], DETAIL)
        self.assertEqual(snapshot["steps"][0]["error_detail"], DETAIL)

    def test_pass_execution_carries_no_detail(self):
        execution = CaseExecution(
            case_id="LOCAL-LENS-02", parent_id="PARENT", status="PASS",
            steps=(StepOutcome(requirement_id="R1", method="m", status="PASS",
                               data=None, evidence={}, error_type=None),),
            completed_count=1, total_count=1, error_type=None,
            ready_to_execute=True, error_detail=None)
        execution.validate()
        snapshot = execution.snapshot()
        self.assertIsNone(snapshot["error_detail"])
        self.assertIsNone(snapshot["steps"][0]["error_detail"])


class WorkspaceLifecycleLensTests(unittest.TestCase):
    def test_blocked_execution_carries_first_error_detail(self):
        profile = ws.profile_value()
        with patch.dict(os.environ, {"BCS_E2E_DRIVER_BOT_TOKEN": ws.TOKEN,
                                     "BCS_E2E_HUMAN_COOKIE": ws.COOKIE}), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=RunStateError(DETAIL)):
            adapter = ws.RemoteWorkspaceGroupLifecycleAdapter(profile)
            execution = ws.execute_workspace_group_lifecycle_case(adapter)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")
        self.assertEqual(execution.error_detail, DETAIL)
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["error_detail"], DETAIL)
        carrying = [step for step in snapshot["steps"]
                    if step["error_detail"] == DETAIL]
        self.assertEqual(len(carrying), 1)
        self.assertEqual(carrying[0]["status"], "NOT_RUN")
        self.assertTrue(all(step["error_detail"] is None
                            for step in snapshot["steps"] if step is not carrying[0]))


class LensStaticTests(unittest.TestCase):
    def test_case_executor_keeps_shared_helper(self):
        source = (RUNNER / "case_executor.py").read_text(encoding="utf-8")
        self.assertIn("def record_error_detail(", source)
        self.assertIn("detail = record_error_detail(error)", source)

    def test_lensed_envelopes_forward_execution_detail(self):
        for name in FORWARDING_FILES:
            source = (RUNNER / name).read_text(encoding="utf-8")
            self.assertIn('"error_detail": execution.error_detail', source, name)


class SpineLensStaticTests(unittest.TestCase):
    def test_lensed_custom_spines_capture_first_failure_detail(self):
        for name in (
                "workspace_group_lifecycle_contract.py",
                "session_file_core_contract.py",
                "local_friend_connection_decision_contract.py",
                "local_friend_public_accept_contract.py",
                "workspace_roundtrip_contract.py",
                "remote_operator_cli_adapter.py"):
            source = (RUNNER / name).read_text(encoding="utf-8")
            self.assertIn("record_error_detail(", source, name)
            self.assertIn("first_error_detail", source, name)
            self.assertIn("error_detail=first_error_detail", source, name)
            self.assertIn("error_detail=detail", source, name)




class CliContractLensStaticTests(unittest.TestCase):
    def test_single_step_custom_contracts_forward_detail(self):
        for name in (
                "workspace_cli_create_get_contract.py",
                "workspace_cli_detail_contract.py",
                "workspace_cli_fuse_contract.py",
                "workspace_cli_list_contract.py",
                "workspace_cli_member_contract.py",
                "workspace_cli_status_contract.py",
                "custom_collaboration_cli_case_run.py"):
            source = (RUNNER / name).read_text(encoding="utf-8")
            self.assertIn("record_error_detail(", source, name)
            self.assertIn("first_error_detail", source, name)
            self.assertIn("error_detail=first_error_detail", source, name)


class ValidatorLensStaticTests(unittest.TestCase):
    def test_strict_step_validators_admit_error_detail_key(self):
        for name in ("local_collaboration_team_reporting.py",
                     "local_session_service_parent_reporting.py"):
            source = (RUNNER / name).read_text(encoding="utf-8")
            self.assertIn("'error_detail'}", source, name)


class WorkspaceCliCreateGetLensTests(unittest.TestCase):
    def test_blocked_first_step_carries_detail(self):
        class BoomAdapter(RemoteWorkspaceCliCreateGetAdapter):
            def create_retained_workspace_group(self):
                raise RunStateError(DETAIL)

        adapter = BoomAdapter(cg.profile_value(), cg.ScriptedRunner())
        execution = execute_workspace_cli_create_get_case(adapter)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")
        self.assertEqual(execution.error_detail, DETAIL)
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["error_detail"], DETAIL)
        self.assertEqual(snapshot["steps"][0]["error_detail"], DETAIL)


if __name__ == "__main__":
    unittest.main()
