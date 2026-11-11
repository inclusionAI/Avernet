"""Command and report tests for the dual Channel product-CLI case."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e import channel_cli_case_run as channel_case_runner
from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.cli import main
from bcs_e2e.channel_cli_case_run import dual_channel_cli_case_run
from bcs_e2e.channel_cli_profile import validate_channel_cli_profile
from bcs_e2e.local_channel_cli_contract import PARENT
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read
from test_remote_channel_cli_adapter import channel_profile_value
from bcs_e2e.channel_cli_profile import validate_channel_cli_profile
from bcs_e2e.channel_cli_runner import ChannelCliRunner

RUN_ID = "channel-cli-run-1"


def local_profile() -> dict:
    return validate_channel_cli_profile(channel_profile_value())


def preprod_profile() -> dict:
    return validate_channel_cli_profile(channel_profile_value(preprod=True))


def fake_execution() -> CaseExecution:
    step = StepOutcome(
        requirement_id="CHANNEL-CLI.group-delete",
        method="delete_cli_group",
        status="PASS",
        data={"group_deleted": True},
        evidence={"credential_kind": "owned_local_bot_token"},
        error_type=None,
    )
    execution = CaseExecution(
        case_id="DUAL-CHANNEL-CLI-01",
        parent_id=PARENT,
        status="PASS",
        steps=(step,),
        completed_count=1,
        total_count=1,
        error_type=None,
        ready_to_execute=True,
    )
    execution.validate()
    return execution


class FakeRunner:
    def __init__(self, profile, *, binary, binary_sha256, state_dir):
        self.profile = profile
        self.state_dir = Path(state_dir).resolve()


class FakeAdapter:
    def __init__(self, profile, runner):
        self.profile = profile
        self.runner = runner


class ChannelCliCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.cli_binary = self.root / "bcs-cli"
        self.cli_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.cli_binary.chmod(0o700)
        self.cli_sha256 = hashlib.sha256(self.cli_binary.read_bytes()).hexdigest()

    def invoke(self, *, preprod=False, profile=None):
        profile = (profile if profile is not None
                   else (preprod_profile() if preprod else local_profile()))
        profile_path = self.root / f"profile-{'preprod' if preprod else 'local'}.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        state_dir = self.root / f"state-{'preprod' if preprod else 'local'}"
        expected = fake_execution()
        with patch.object(channel_case_runner, "ChannelCliRunner", FakeRunner), \
             patch.object(channel_case_runner, "RemoteChannelCliAdapter", FakeAdapter), \
             patch.object(channel_case_runner, "execute_channel_cli_case",
                          lambda adapter: expected):
            report = dual_channel_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, profile, state_dir

    def test_report_is_registered_in_the_case_inventory(self):
        rows = {row["case_id"] for row in migration_plan()[
            "cross_environment_product_cli_case_slices"]}
        self.assertIn("DUAL-CHANNEL-CLI-01", rows)

    def test_local_run_writes_bound_sanitized_receipts(self):
        report, profile, state_dir = self.invoke()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], "DUAL-CHANNEL-CLI-01")
        self.assertEqual(report["credential_kind"], "owned_local_bot_token")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(read(state_dir / "channel-cli-profile.json"), profile)
        self.assertEqual(read(state_dir / "channel-cli-binding.json")["case_id"],
                         "DUAL-CHANNEL-CLI-01")
        self.assertEqual(read(state_dir / "channel-cli-run.json"), report)
        raw = json.dumps(report, sort_keys=True)
        self.assertNotIn("ceo-owned-local-token", raw)
        self.assertNotIn("pm-owned-local-token", raw)
        self.assertNotIn("eng-owned-local-token", raw)

    def test_preprod_report_records_cookie_kind_without_secret(self):
        report, _, _ = self.invoke(preprod=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"], "human_cookie")
        self.assertEqual(report["environment"]["entrypoint_kind"], "gateway")
        self.assertNotIn("human-cookie", json.dumps(report, sort_keys=True))

    def test_preprod_role_token_profile_records_role_credential_kind(self):
        report, _, _ = self.invoke(preprod=True,
                                   profile=role_token_preprod_profile())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(
            report["credential_kind"], "human_cookie_with_owned_role_bot_token")
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("human-cookie", serialized)
        self.assertNotIn("driver-role-token", serialized)

    def test_permissions_and_fresh_state_are_enforced(self):
        state_dir = self.root / "state"
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(local_profile()), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_channel_cli_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, state_dir=state_dir,
                run_id=RUN_ID, allow_write=True, confirm_run_id="other")
        self.assertFalse(state_dir.exists())
        state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_channel_cli_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, state_dir=state_dir,
                run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID)

    def test_cli_dispatch_uses_the_dual_channel_runner(self):
        state_dir = self.root / "dispatch"
        profile_path = self.root / "dispatch-profile.json"
        profile_path.write_text(json.dumps(local_profile()), encoding="utf-8")
        arguments = [
            "dual-channel-run",
            "--profile", str(profile_path),
            "--cli-binary", str(self.cli_binary),
            "--cli-binary-sha256", self.cli_sha256,
            "--state-dir", str(state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch("bcs_e2e.cli.dual_channel_cli_case_run",
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


class ChannelCliPreprodRoleTokenTests(unittest.TestCase):
    """Preprod owned per-role Bot token credential; dual-environment symmetry."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))
        self.cli_binary = self.root / "bcs-cli"
        self.cli_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.cli_binary.chmod(0o700)
        self.cli_sha256 = hashlib.sha256(
            self.cli_binary.read_bytes()).hexdigest()
        self.state = self.root / "state"
        self.state.mkdir()

    def test_validator_admits_role_token_shape_and_rejects_drift(self):
        value = channel_profile_value(preprod=True)
        value["auth"] = {
            "kind": "human-cookie-with-owned-role-bot-token",
            "cookie_env": "BCS_E2E_HUMAN_COOKIE",
            "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
            "member_token_env": "BCS_E2E_MEMBER_BOT_TOKEN",
        }
        validate_channel_cli_profile(value)
        value["auth"]["eng_token_env"] = "BCS_E2E_ENG_BOT_TOKEN"
        validate_channel_cli_profile(value)
        value["auth"]["mystery_env"] = "BCS_E2E_X"
        with self.assertRaises(ConfigError):
            validate_channel_cli_profile(value)

    def test_credentials_and_context_carry_owned_role_token(self):
        profile = role_token_preprod_profile()
        runner = ChannelCliRunner(
            profile, binary=self.cli_binary, binary_sha256=self.cli_sha256,
            state_dir=self.state)
        env = {"BCS_E2E_DRIVER_BOT_TOKEN": "driver-role-token",
               "BCS_E2E_MEMBER_BOT_TOKEN": "member-role-token",
               "BCS_E2E_ENG_BOT_TOKEN": "eng-role-token",
               "BCS_E2E_HUMAN_COOKIE": "human-cookie"}
        with patch.dict("os.environ", env):
            for role in ("driver", "member", "eng"):
                kind, credential = runner._credentials(role)
                self.assertEqual(
                    kind, "human_cookie_with_owned_role_bot_token")
                self.assertEqual(credential, f"{role}-role-token")
            root, context, environment = runner._context(
                "member", "human_cookie_with_owned_role_bot_token",
                "member-role-token")
            empty = dict(env)
            empty["BCS_E2E_ENG_BOT_TOKEN"] = ""
            with patch.dict("os.environ", empty):
                self.assertEqual(
                    runner._credentials("eng"),
                    ("human_cookie", "human-cookie"))
        session = json.loads(context.read_text(encoding="utf-8"))
        self.assertEqual(session["token"], "member-role-token")
        self.assertEqual(
            session["bot_uuid"], profile["actors"]["member"]["bot_id"])
        self.assertEqual(environment["BCS_COOKIE"], "human-cookie")
        self.assertEqual(environment["tc_sdb_nenv"], "production")

    def test_default_preprod_profile_keeps_cookie_only_context(self):
        profile = preprod_profile()
        runner = ChannelCliRunner(
            profile, binary=self.cli_binary, binary_sha256=self.cli_sha256,
            state_dir=self.state)
        with patch.dict("os.environ", {"BCS_E2E_HUMAN_COOKIE": "human-cookie"}):
            self.assertEqual(
                runner._credentials("driver"), ("human_cookie", "human-cookie"))


def role_token_preprod_profile() -> dict:
    value = channel_profile_value(preprod=True)
    value["auth"] = {
        "kind": "human-cookie-with-owned-role-bot-token",
        "cookie_env": "BCS_E2E_HUMAN_COOKIE",
        "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
        "member_token_env": "BCS_E2E_MEMBER_BOT_TOKEN",
        "eng_token_env": "BCS_E2E_ENG_BOT_TOKEN",
    }
    return validate_channel_cli_profile(value)


if __name__ == "__main__":
    unittest.main()
