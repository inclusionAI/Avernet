"""Command and report tests for the dual collaboration-team CLI runner."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))

from bcs_e2e.cli import main
from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.collaboration_team_case_run import (
    REPORT_CONTRACT,
    dual_collaboration_team_cli_case_run,
)
from bcs_e2e.collaboration_team_profile import (
    validate_collaboration_team_profile,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read

from test_collaboration_team import profile_value

RUN_ID = "collaboration-team-cli-run-1"
PARENT_ID = "story_cli_operator_builds_collaboration_team"
CLUSTER_CASE_ID = "DUAL-COLLABORATION-TEAM-CLI-01"


def execution(*, gateway=False) -> CaseExecution:
    step = StepOutcome(
        requirement_id="CLI-COLLAB-TEAM.protect-visibility",
        method="protect_customer_service_visibility",
        status="PASS",
        data={"case_entry": "collaboration-team"},
        evidence={
            "transport": "PRODUCT_CLI",
            "credential_kind": (
                "human_cookie" if gateway else "owned_local_bot_token"
            ),
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=None,
    )
    value = CaseExecution(
        case_id=CLUSTER_CASE_ID,
        parent_id=PARENT_ID,
        status="PASS",
        steps=(step,),
        completed_count=1,
        total_count=1,
        error_type=None,
        ready_to_execute=True,
    )
    value.validate()
    return value


class CollaborationTeamCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        self.cli_binary = self.root / "bcs-cli"
        self.cli_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.cli_binary.chmod(0o700)
        self.cli_sha256 = hashlib.sha256(
            self.cli_binary.read_bytes()).hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *, gateway=False, profile=None):
        profile_path = self.root / "profile.json"
        profile_path.write_text(
            json.dumps(profile if profile is not None
                       else profile_value(gateway=gateway)), encoding="utf-8")
        state_dir = self.root / "run-state"
        expected = execution(gateway=gateway)

        class FakeRunner:
            def __init__(self, profile, *, binary, binary_sha256, state_dir):
                self.profile = profile
                self.binary = binary
                self.binary_sha256 = binary_sha256
                self.state_dir = state_dir

        class FakeAdapter:
            def __init__(self, runner):
                self.runner = runner
                self.profile = runner.profile

        with patch(
                "bcs_e2e.collaboration_team_case_run.CollaborationTeamRunner",
                FakeRunner), \
             patch(
                "bcs_e2e.collaboration_team_case_run.RemoteCollaborationTeamAdapter",
                FakeAdapter), \
             patch(
                "bcs_e2e.collaboration_team_case_run.execute_collaboration_team_case",
                lambda adapter: expected):
            report = dual_collaboration_team_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, expected, state_dir

    def test_local_case_writes_a_bound_sanitized_report(self):
        report, expected, state_dir = self.invoke()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], expected.case_id)
        self.assertEqual(report["parent_id"], PARENT_ID)
        self.assertEqual(report["contract"], REPORT_CONTRACT)
        self.assertEqual(report["credential_kind"], "owned_local_bot_token")
        self.assertEqual(report["resource_disposition"],
                         "REQUEST_AND_GROUP_HISTORY_RETAINED")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(report["case"], expected.snapshot())
        self.assertEqual(read(state_dir / "collaboration-team-profile.json"),
                         profile_value(gateway=False))
        self.assertEqual(
            read(state_dir / "collaboration-team-binding.json")["case_id"],
            expected.case_id)
        self.assertEqual(read(state_dir / "collaboration-team-run.json"), report)
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("private-driver-token", serialized)
        self.assertNotIn("owned-local-bot-tokens", serialized)

    def test_gateway_reports_cookie_kind_without_cookie_secret(self):
        report, _, _ = self.invoke(gateway=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(
            report["credential_kind"],
            "human_cookie_with_owned_driver_bot_token")
        self.assertEqual(report["environment"]["tier"], "preprod")
        self.assertEqual(report["environment"]["entrypoint_kind"], "gateway")
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("private-human-cookie-value", serialized)
        self.assertNotIn("human-cookie", serialized)

    def test_preprod_role_token_profile_records_role_credential_kind(self):
        value = profile_value(gateway=True)
        value["auth"] = {
            "kind": "human-cookie-with-owned-role-bot-token",
            "cookie_env": "BCS_E2E_HUMAN_COOKIE",
            "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
            "engineering_token_env": "BCS_E2E_ENG_BOT_TOKEN",
            "customer_service_token_env": "BCS_E2E_CS_BOT_TOKEN",
        }
        profile = validate_collaboration_team_profile(value)
        report, _, _ = self.invoke(gateway=True, profile=profile)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(
            report["credential_kind"], "human_cookie_with_owned_role_bot_token")
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("private-human-cookie-value", serialized)
        self.assertNotIn("private-driver-token", serialized)

    def test_permissions_and_fresh_state_are_enforced(self):
        profile_path = self.root / "guard-profile.json"
        profile_path.write_text(
            json.dumps(profile_value(gateway=False)), encoding="utf-8")
        state_dir = self.root / "guard-state"
        with self.assertRaisesRegex(
                ConfigError, "exact Run confirmation"):
            dual_collaboration_team_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id="other",
            )
        self.assertFalse(state_dir.exists())
        state_dir.mkdir()
        with self.assertRaisesRegex(
                ConfigError, "fresh private state directory"):
            dual_collaboration_team_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_cli_dispatch_uses_the_collaboration_team_runner(self):
        profile_path = self.root / "dispatch-profile.json"
        profile_path.write_text(
            json.dumps(profile_value(gateway=False)), encoding="utf-8")
        state_dir = self.root / "dispatch-state"
        arguments = [
            "dual-collaboration-team-run",
            "--profile", str(profile_path),
            "--cli-binary", str(self.cli_binary),
            "--cli-binary-sha256", self.cli_sha256,
            "--state-dir", str(state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch(
                "bcs_e2e.cli.dual_collaboration_team_cli_case_run",
                return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=profile_path,
            cli_binary=self.cli_binary,
            cli_binary_sha256=self.cli_sha256,
            state_dir=state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
