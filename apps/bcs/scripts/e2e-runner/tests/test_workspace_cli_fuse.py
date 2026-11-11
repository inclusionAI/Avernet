"""Cross-environment WORKSPACE.cli-fuse product-CLI tests; no live request."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.workspace_cli_fuse_contract import (
    RemoteWorkspaceCliFuseAdapter,
    CASE_ID,
    execute_workspace_cli_fuse_case,
    workspace_cli_fuse_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
    validate_workspace_cli_fuse_profile,
)
from bcs_e2e.workspace_cli_runner import WorkspaceCliRunner, WorkspaceCliRunnerError

DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_workspace-fuse-01"
SESSION = GROUP + ":01020304"
TOKEN = "driver-token-secret"
COOKIE = "cookie-secret"


def profile_value(*, gateway=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-workspace-cli-fuse-profile-v1",
        "environment": {"tier": "local" if not gateway else "preprod",
                        "id": "workspace-fuse-local" if not gateway else "workspace-fuse-preprod",
                        "expected_bcs_env": "local" if not gateway else "pre"},
        "entrypoint": {"kind": "singlebox" if not gateway else "gateway",
                       "origin": "http://127.0.0.1:21000" if not gateway else "https://gateway.example.com",
                       "api_face": "legacy", "manifest_face": "legacy"},
        "human": {"user_id": "owner-1"},
        "actors": {"driver": {"bot_id": DRIVER, "owner_id": "owner-1",
                              "dedicated": True, "visibility": "public"},
                   "member": {"bot_id": MEMBER, "owner_id": "owner-1",
                              "dedicated": True, "visibility": "public"}},
        "auth": ({"kind": "owned-local-bot-tokens", "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}
                 if not gateway else {"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}),
        "relationships": {"group": "bounded-owned-bots-retained-cli-create-fuse",
                          "initial_session": "retained", "cleanup": "none",
                          "participant_mode": "driver-member"},
        "policy": {"fixture_pool": "workspace-cli-fuse-pool", "exclusive_use_confirmed": True,
                    "timeout_seconds": 10, "max_response_bytes": 65536,
                    "failure_retention_hours": 24},
        "cli": {"transport": "product-cli", "command_family": "group", "api_face": "legacy"},
    }
    if mutate is not None:
        mutate(value)
    return value


def create_output():
    return (f"Group created:\n  ID: {GROUP}\n  Driver: {DRIVER}\n"
            f"  Participants: {DRIVER},{MEMBER}\n  Session: {SESSION}\n").encode()


WARM_PERSPECTIVES = "warm"


def fusion_output(*, recommendation="Simple fusion for: how to align?",
                   perspectives=WARM_PERSPECTIVES, prefix=True):
    if perspectives == WARM_PERSPECTIVES:
        perspectives = [
            {"bot_uuid": DRIVER, "name": "driver", "emoji": "robot",
             "summary": "driver workspace context"},
            {"bot_uuid": MEMBER, "name": "member", "emoji": "robot",
             "summary": "member workspace context"},
        ]
    value = {"perspectives": perspectives, "conflicts": [],
             "alignment_points": ["participants agree on delivery"],
             "recommendation": recommendation,
             "key_insights": ["shared workspace context"],
             "extra": {"fusion_id": "fus-workspace-cli-01", "fusion_mode": "agent"}}
    prefix_bytes = b"Fusion result:\n" if prefix else b""
    return prefix_bytes + json.dumps(value).encode()


class WorkspaceCliFuseTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.state = self.root / "state"
        self.state.mkdir()
        self.env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN, "BCS_E2E_HUMAN_COOKIE": COOKIE}

    def run_case(self, *, gateway=False, fusion=None, returncode=0):
        profile = validate_workspace_cli_fuse_profile(profile_value(gateway=gateway))
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                    binary_sha256=self.digest, state_dir=self.state)
        commands = []
        contexts = []

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            environment = kwargs["environment"]
            context_path = Path(environment["BOT_DATA_DIR"]) / ".bcs" / "session.json"
            contexts.append((json.loads(context_path.read_text()),
                              environment.get("BCS_COOKIE")))
            if len(commands) == 1:
                stdout = create_output()
            else:
                stdout = fusion if fusion is not None else fusion_output()
            return subprocess.CompletedProcess(cmd, returncode, stdout, b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture):
            execution = execute_workspace_cli_fuse_case(
                RemoteWorkspaceCliFuseAdapter(profile, runner))
        return execution, runner, commands, contexts

    def test_error_gap_carries_the_contract_message(self):
        raw = (b"Fusion result:\n" + json.dumps({
            "perspectives": [], "conflicts": [], "alignment_points": [],
            "recommendation": None, "key_insights": [],
            "extra": {"fusion_id": "fus-x", "fusion_mode": "agent"}}).encode())
        execution, _, _, _ = self.run_case(fusion=raw)
        self.assertEqual(execution.status, "BLOCKED")
        step = execution.snapshot()["steps"][0]
        self.assertEqual(step["error_type"], "RunStateError")
        self.assertTrue(step["error_gap"])

    def test_case_guard_accepts_the_fusion_contract(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = (ROOT / "bcs_e2e/workspace_cli_profile.py",
                  ROOT / "bcs_e2e/workspace_cli_runner.py",
                  ROOT / "bcs_e2e/workspace_cli_fuse_contract.py")
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                 text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_adaptability_and_inventory_do_not_grant_credit(self):
        value = workspace_cli_fuse_adaptability()
        self.assertEqual(value["case_id"], CASE_ID)
        self.assertEqual(value["requirement_ids"], ["WORKSPACE.cli-fuse"])
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        self.assertIn(CASE_ID, [row["case_id"] for row in rows])
        row = next(row for row in rows if row["case_id"] == CASE_ID)
        self.assertEqual(row["environments"], ["singlebox-product-cli", "preprod-product-cli"])

    def test_local_sequence_requires_exact_two_bot_fusion(self):
        execution, runner, commands, contexts = self.run_case()
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(runner.executed, ["fuse.group.create", "fuse.group.fuse"])
        self.assertEqual(commands[1][-7:],
                         ["fuse", "--group", GROUP, "--question", "how to align?",
                          "--participants", f"{DRIVER},{MEMBER}"])
        for context, cookie in contexts:
            self.assertEqual(context["token"], TOKEN)
            self.assertIsNone(cookie)
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["steps"][0]["data"]["group_id"], GROUP)
        self.assertEqual(snapshot["steps"][0]["data"]["participant_bot_ids"], [DRIVER, MEMBER])
        self.assertEqual(snapshot["steps"][0]["data"]["recommendation"],
                         "Simple fusion for: how to align?")
        self.assertEqual(snapshot["steps"][0]["data"]["perspectives_count"], 2)
        self.assertFalse(snapshot["steps"][0]["data"]["cold_context"])
        self.assertEqual(len(snapshot["steps"][0]["evidence"]["requests"]), 2)
        self.assertNotIn(TOKEN, json.dumps(snapshot, sort_keys=True))

    def test_cold_context_empty_perspectives_passes(self):
        execution, _, _, _ = self.run_case(fusion=fusion_output(perspectives=[]))
        self.assertEqual(execution.status, "PASS")
        data = execution.snapshot()["steps"][0]["data"]
        self.assertEqual(data["perspectives_count"], 0)
        self.assertTrue(data["cold_context"])
        self.assertEqual(data["participant_bot_ids"], [])

    def test_gateway_cookie_is_not_evidence(self):
        execution, _, _, contexts = self.run_case(gateway=True)
        self.assertEqual(execution.status, "PASS")
        for context, cookie in contexts:
            self.assertEqual(context["token"], "")
            self.assertEqual(cookie, COOKIE)
        self.assertNotIn(COOKIE, json.dumps(execution.snapshot(), sort_keys=True))

    def test_inexact_fusion_result_blocks_without_partial_credit(self):
        cases = {
            "missing-prefix": fusion_output(prefix=False),
            "null-recommendation": (b"Fusion result:\n" + json.dumps({
                "perspectives": [], "conflicts": [], "alignment_points": [],
                "recommendation": None, "key_insights": [],
                "extra": {"fusion_id": "fus-x", "fusion_mode": "agent"}}).encode()),
            "non-string-recommendation": (b"Fusion result:\n" + json.dumps({
                "perspectives": [], "conflicts": [], "alignment_points": [],
                "recommendation": 123, "key_insights": [],
                "extra": {"fusion_id": "fus-x", "fusion_mode": "agent"}}).encode()),
            "empty-recommendation": fusion_output(recommendation=""),
            "one-perspective": fusion_output(perspectives=[
                {"bot_uuid": DRIVER, "summary": "only one"}]),
            "wrong-participant": fusion_output(perspectives=[
                {"bot_uuid": DRIVER, "summary": "d"},
                {"bot_uuid": "other", "summary": "m"}]),
            "missing-summary": fusion_output(perspectives=[
                {"bot_uuid": DRIVER, "summary": ""}, {"bot_uuid": MEMBER, "summary": "m"}]),
            "same-summary": fusion_output(perspectives=[
                {"bot_uuid": DRIVER, "summary": "same"},
                {"bot_uuid": MEMBER, "summary": "same"}]),
            "unknown-server-key": (b"Fusion result:\n" + json.dumps({
                "group_id": None, "perspectives": [], "conflicts": [],
                "alignment_points": [], "recommendation": "r",
                "key_insights": [], "extra": {"fusion_id": "fus-x",
                                               "fusion_mode": "agent"},
                "surprise": 1}).encode()),
            "missing-extra": (b"Fusion result:\n" + json.dumps({
                "perspectives": [], "conflicts": [], "alignment_points": [],
                "recommendation": "r", "key_insights": []}).encode()),
            "arbitrary-json": b"Fusion result:\n{}\n",
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                execution, _, _, _ = self.run_case(fusion=raw)
                self.assertEqual(execution.status, "BLOCKED")
                self.assertEqual(execution.completed_count, 0)

    def test_nonzero_fusion_exit_is_rejected(self):
        execution, _, _, _ = self.run_case(returncode=1)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 0)

    def test_fusion_create_is_one_shot(self):
        profile = validate_workspace_cli_fuse_profile(profile_value())
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                    binary_sha256=self.digest, state_dir=self.state)

        def fake_capture(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, create_output(), b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture):
            runner.execute("fuse.group.create")
            with self.assertRaisesRegex(WorkspaceCliRunnerError, "already executed"):
                runner.execute("fuse.group.create")

    def test_profile_rejects_unadmitted_fusion_effects(self):
        cases = {
            "cleanup": lambda value: value["relationships"].update(cleanup="automatic"),
            "delete-effect": lambda value: value["relationships"].update(
                group="bounded-owned-bots-cli-delete"),
            "borrowed-bot": lambda value: value["actors"]["member"].update(dedicated=False),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_workspace_cli_fuse_profile(profile_value(mutate=mutate))


    def test_default_profile_three_segment_perspective_owner_is_exact(self):
        # The station builds participant ids as f"{bot_id}:{profile_id}" with
        # the fusion profile id defaulting to "default" (bcs-fuse
        # fuse_backed.rs), so a default-profile warm perspective travels back
        # as the three-segment "<bot>:default". Admit exactly that suffix.
        execution, _, _, _ = self.run_case(fusion=fusion_output(perspectives=[
            {"bot_uuid": f"{DRIVER}:default", "name": "driver", "emoji": "robot",
             "summary": "driver workspace context"},
            {"bot_uuid": f"{MEMBER}:default", "name": "member", "emoji": "robot",
             "summary": "member workspace context"}]))
        self.assertEqual(execution.status, "PASS")
        data = execution.snapshot()["steps"][0]["data"]
        self.assertEqual(data["participant_bot_ids"],
                         [f"{DRIVER}:default", f"{MEMBER}:default"])
        self.assertEqual(data["perspectives_count"], 2)
        self.assertFalse(data["cold_context"])

    def test_nondefault_profile_extra_segment_stays_inexact(self):
        # Only the default profile segment may be elided; any other suffix
        # fails closed.
        execution, _, _, _ = self.run_case(fusion=fusion_output(perspectives=[
            {"bot_uuid": f"{DRIVER}:custom", "name": "driver", "emoji": "robot",
             "summary": "driver workspace context"},
            {"bot_uuid": f"{MEMBER}:custom", "name": "member", "emoji": "robot",
             "summary": "member workspace context"}]))
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 0)
        step = execution.snapshot()["steps"][0]
        self.assertEqual(step["error_type"], "RunStateError")
        self.assertIn("warm perspectives are not exact", step["error_gap"] or "")


if __name__ == "__main__":
    unittest.main()
