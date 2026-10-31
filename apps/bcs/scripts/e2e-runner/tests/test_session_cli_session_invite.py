"""Cross-environment SESSION.cli-session-invite product-CLI tests; no live request."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))
from bcs_e2e.workspace_cli_runner import WorkspaceCliRunner
from bcs_e2e.workspace_cli_session_invite import (
    SESSION_CLI_SESSION_INVITE_CASE_ID,
    RemoteSessionCliSessionInviteAdapter,
    execute_session_cli_session_invite_case,
    session_cli_session_invite_adaptability,
)
from bcs_e2e.session_cli_profile import (
    SESSION_CLI_SESSION_INVITE_CREATE_OPERATION,
    SESSION_CLI_SESSION_INVITE_JOIN_OPERATION,
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_GROUP_INVITE_CREATE_OPERATION,
    SESSION_CLI_GROUP_INVITE_JOIN_OPERATION,
    SESSION_CLI_BOT_GROUPS_OPERATION,
    validate_session_cli_create_get_profile,
)

DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_session_cli_03"
SESSION = GROUP + ":01020304"
TOKEN = "owned-local-token"
COOKIE = "human-cookie"
INVITE_TOKEN = "invite-placeholder-token"
EXPIRES_AT = int(time.time()) + 300

def profile_value(*, preprod=False):
    tier = "preprod" if preprod else "local"
    return {
        "schema_version": 1,
        "contract": "dual-session-cli-create-list-get-profile-v1",
        "environment": {
            "tier": tier,
            "id": "session-cli-preprod" if preprod else "session-cli-local",
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": {
            "kind": "gateway" if preprod else "singlebox",
            "origin": "https://gateway.example.com" if preprod else "http://127.0.0.1:21000",
            "api_face": "legacy", "manifest_face": "legacy",
        },
        "human": {"user_id": "39464"},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": "39464",
                        "dedicated": True, "visibility": "public"},
            "member": {"bot_id": MEMBER, "owner_id": "39464",
                        "dedicated": True, "visibility": "public"},
        },
        "auth": {
            "kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE",
        } if preprod else {
            "kind": "owned-local-bot-tokens",
            "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
        },
        "relationships": {
            "group": "bounded-owned-bots-cli-session-create-list-get",
            "initial_session": "deleted-with-group",
            "explicit_session": "created-listed-read-deleted-with-group",
            "cleanup": "exact-owned-group", "participant_mode": "driver-member",
        },
        "policy": {
            "fixture_pool": "session-cli-pool", "exclusive_use_confirmed": True,
            "timeout_seconds": 10, "max_response_bytes": 65536,
            "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-cli", "command_family": "session", "api_face": "legacy"},
        "route_bindings": {
            SESSION_CLI_GROUP_DELETE_OPERATION: {
                "method": "DELETE", "path": "/groups/{group_id}?bot_id={driver_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION: {
                "method": "GET", "path": "/groups/{group_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_BOT_GROUPS_OPERATION: {
                "method": "GET", "path": "/bots/{driver_actor_id}/groups?limit=50&group_kind=all",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_GROUP_INVITE_CREATE_OPERATION: {
                "method": "POST", "path": "/groups/{group_id}/invite-link",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_GROUP_INVITE_JOIN_OPERATION: {
                "method": "POST", "path": "/groups/join/{invite_token}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_INVITE_CREATE_OPERATION: {
                "method": "POST", "path": "/sessions/{session_id}/invite-link",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_INVITE_JOIN_OPERATION: {
                "method": "POST", "path": "/sessions/join/{invite_token}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
        },
    }

def group_output():
    return (f"Group created:\n  ID: {GROUP}\n  Driver: {DRIVER}\n"
             f"  Participants: {DRIVER},{MEMBER}\n"
             f"  Session: {GROUP}:00000001\n").encode()

def session_output():
    return json.dumps({
        "id": SESSION, "session_id": SESSION, "group_id": GROUP,
        "session_title": "cli-e2e-sess",
    }).encode()

def invite_output(origin, *, foreign=False, missing=False):
    if origin == "https://gateway.example.com":
        origin = "https://frontend.example.com"
    join_url = ("https://foreign.invalid/sessions/join/" + INVITE_TOKEN if foreign else
                origin + "/bcn/chat/invite/sessions/" + INVITE_TOKEN)
    data = {"expires_at": EXPIRES_AT, "join_url": join_url}
    return json.dumps({"invite_token": INVITE_TOKEN, **data}).encode() if not missing else b"{}"

class SessionInviteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = self.root / "state"
        self.state.mkdir()
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def run_case(self, *, preprod=False, foreign=False, missing=False):
        profile = validate_session_cli_create_get_profile(profile_value(preprod=preprod))
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                   binary_sha256=self.digest, state_dir=self.state)
        commands, contexts, outputs = [], [], [
            session_output(),
            invite_output(profile["entrypoint"]["origin"], foreign=foreign, missing=missing),
        ]

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            if len(commands) == 1:
                result = group_output()
            else:
                result = outputs[len(commands) - 2]
            contexts.append((kwargs["environment"]["HOME"], kwargs["environment"].get("BCS_COOKIE")))
            return subprocess.CompletedProcess(cmd, 0, result, b"")

        def fake_call(self_client, operation, **kwargs):
            if operation == SESSION_CLI_GROUP_DELETE_OPERATION:
                return ({"deleted": True, "id": kwargs["group_id"]}, {
                    "operation": operation, "http_status": 200,
                    "path": "/groups/" + kwargs["group_id"],
                })
            return ({"status": 404, "code": "group_not_found",
                    "message": "Group not found",
                    "params": {"group_id": kwargs["group_id"]}}, {
                "operation": operation, "http_status": 404,
                "path": "/groups/" + kwargs["group_id"],
            })

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN, "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture), \
             patch("bcs_e2e.write_transport.WriteClient.call", side_effect=fake_call, autospec=True):
            result = execute_session_cli_session_invite_case(
                RemoteSessionCliSessionInviteAdapter(profile, runner)
            )
        return result, commands, contexts

    def test_local_and_preprod_mint_and_cleanup(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                result, commands, contexts = self.run_case(preprod=preprod)
                self.assertEqual(result.status, "PASS")
                self.assertIn("session-invite", result.snapshot()["steps"][0]["evidence"]["requests"][2]["case_class"])
                self.assertEqual(commands[2][-3:], [SESSION, "--ttl-seconds", "300"])
                self.assertNotIn(INVITE_TOKEN, json.dumps(result.snapshot(), sort_keys=True))
                if preprod:
                    self.assertEqual(contexts[0][1], COOKIE)
                else:
                    self.assertIsNone(contexts[0][1])
                # Launcher-root contract (workspace_cli_runner._context): the frozen
                # simulator-wrapper product CLI gets a private bind-mountable root under
                # ~/.bcs-e2e/runner-<uuid> (Colima shares only $HOME); roots are 0700,
                # per-operation unique, and swept after 24h. Pin the new contract.
                launch_base = str(WorkspaceCliRunner._LAUNCHER_ROOT_BASE)
                self.assertTrue(all(context[0].startswith(launch_base + "/runner-") for context in contexts))
                self.assertEqual(len({context[0] for context in contexts}), len(contexts))
                self.assertNotIn(COOKIE, json.dumps(result.snapshot(), sort_keys=True))

    def test_foreign_invite_url_is_blocked(self):
        result, _, _ = self.run_case(foreign=True)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_missing_invite_fields_are_blocked(self):
        result, _, _ = self.run_case(missing=True)
        self.assertEqual(result.status, "BLOCKED")

    def test_case_guard_accepts_the_runner_and_profile(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = [ROOT / "bcs_e2e/workspace_cli_profile.py", ROOT / "bcs_e2e/workspace_cli_runner.py"]
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_adaptability_is_local_and_remote(self):
        value = session_cli_session_invite_adaptability()
        self.assertEqual(value["case_id"], SESSION_CLI_SESSION_INVITE_CASE_ID)
        self.assertEqual(value["requirement_ids"], ["SESSION.cli-session-invite"])
        self.assertEqual(value["environments"], ["singlebox-product-cli", "preprod-product-cli"])
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])

if __name__ == "__main__":
    unittest.main()
