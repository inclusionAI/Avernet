"""Cross-environment Channel management product-CLI tests; no live request."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TEST_ROOT = Path(__file__).resolve().parent
ROOT = TEST_ROOT.parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e.channel_cli_runner import ChannelCliRunner, ChannelCliRunnerError
from bcs_e2e.channel_cli_profile import (
    CHANNEL_CLI_CONVERSATION_EMPTY_OPERATION,
    CHANNEL_CLI_GROUP_CREATE_OPERATION,
    CHANNEL_CLI_GROUP_DELETE_OPERATION,
    CHANNEL_CLI_SESSION_CREATE_OPERATION,
    CHANNEL_CLI_SESSION_DELETE_OPERATION,
    validate_channel_cli_profile,
)
from bcs_e2e.remote_channel_cli_adapter import (
    CHANNEL_CLI_CASE_ID,
    RemoteChannelCliAdapter,
    channel_cli_adaptability,
    channel_cli_case_plan,
    execute_channel_cli_case,
)
from bcs_e2e.channel_cli_profile import ConfigError
from bcs_e2e.migration_inventory import migration_plan

DRIVER = "ceo-bot"
MEMBER = "pm-bot"
ENG = "eng-bot"
GROUP = "bcs_grp_channel_cli_04"
INITIAL_SESSION = GROUP + ":00000001"
SESSION = GROUP + ":05060708"
DRIVER_TOKEN = "ceo-owned-local-token"
MEMBER_TOKEN = "pm-owned-local-token"
ENG_TOKEN = "eng-owned-local-token"
COOKIE = "human-cookie"
BRIDGE_ERROR = (
    '{"code":"bad_request","message":"channel bridge is disabled '
    'for this deployment","request_id":"e2e-bind"}').encode()


def channel_profile_value(*, preprod=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-channel-cli-profile-v1",
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": "channel-cli-preprod" if preprod else "channel-cli-local",
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": {
            "kind": "gateway" if preprod else "singlebox",
            "origin": "https://teamclawgw.example.com" if preprod
            else "http://127.0.0.1:21100",
            "api_face": "legacy", "manifest_face": "legacy",
        },
        "human": {"user_id": "39464"},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": "39464",
                       "dedicated": True, "visibility": "public"},
            "member": {"bot_id": MEMBER, "owner_id": "39464",
                       "dedicated": True, "visibility": "public"},
            "eng": {"bot_id": ENG, "owner_id": "39464",
                    "dedicated": True, "visibility": "public"},
        },
        "auth": ({"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
                 if preprod else
                 {"kind": "owned-local-bot-tokens",
                  "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
                  "member_token_env": "BCS_E2E_MEMBER_BOT_TOKEN",
                  "eng_token_env": "BCS_E2E_ENG_BOT_TOKEN"}),
        "relationships": {
            "bindings": "no-durable-binding",
            "group": "bounded-owned-bots-cli-channel",
            "initial_session": "deleted-with-group",
            "explicit_session": "deleted-with-group",
            "cleanup": "exact-owned-sessions-then-group",
            "participant_mode": "member-and-engine",
        },
        "policy": {
            "fixture_pool": "channel-cli-pool", "exclusive_use_confirmed": True,
            "timeout_seconds": 10, "max_response_bytes": 65536,
            "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-cli", "command_family": "channel",
                "api_face": "legacy"},
        "route_bindings": {
            CHANNEL_CLI_SESSION_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/sessions/{session_id}?bot_id={member_actor_id}",
                "api_face": "legacy", "auth": "case_owned_member",
                "response": "legacy-json", "traceparent": "disabled"},
            CHANNEL_CLI_GROUP_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/groups/{group_id}?bot_id={member_actor_id}",
                "api_face": "legacy", "auth": "case_owned_member",
                "response": "legacy-json", "traceparent": "disabled"},
        },
    }
    if mutate is not None:
        mutate(value)
    return value


def bind_failure_output():
    return subprocess.CompletedProcess(
        ["bcs-cli"], 1, b"", BRIDGE_ERROR)


def list_output(items=None):
    return subprocess.CompletedProcess(
        ["bcs-cli"], 0,
        json.dumps({"items": [] if items is None else items}).encode(), b"")


def group_output():
    return subprocess.CompletedProcess(
        ["bcs-cli"], 0, (
            f"Group created:\n  ID: {GROUP}\n  Driver: {MEMBER}\n"
            f"  Participants: {MEMBER},{ENG}\n"
            f"  Session: {INITIAL_SESSION}\n").encode(), b"")


def session_output():
    return subprocess.CompletedProcess(
        ["bcs-cli"], 0, json.dumps({
            "id": SESSION, "session_id": SESSION, "group_id": GROUP,
            "session_title": "CLI channel lookup",
        }).encode(), b"")


def conversation_output():
    return subprocess.CompletedProcess(
        ["bcs-cli"], 0, json.dumps({"items": []}).encode(), b"")


def unbind_output():
    return subprocess.CompletedProcess(
        ["bcs-cli"], 0, json.dumps({"ok": True}).encode(), b"")


def deletion_record(operation, path):
    return {
        "operation": operation, "http_status": 200, "method": "DELETE",
        "path": path, "submitted_request_id": "e2e-channel-cli-delete",
        "traceparent_mode": "disabled", "principal_sent": False,
        "credentials_sent": True, "authorization_sent": True,
    }


class ChannelCliCaseTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = self.root / "state"
        self.state.mkdir()
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def run_case(self, *, preprod=False, leak_secret=False,
                 missing_diagnostic=False, phantom=None,
                 phantom_self=False,
                 conversation_items=None, group_drift=False,
                 session_drift=False, after_drift=False,
                 session_delete_drift=False, group_delete_drift=False):
        profile = validate_channel_cli_profile(
            channel_profile_value(preprod=preprod))
        runner = ChannelCliRunner(profile, binary=self.binary,
                                   binary_sha256=self.digest,
                                   state_dir=self.state)
        commands, contexts = [], []
        outputs = [group_output(), session_output(),
                   conversation_output(), unbind_output(),
                   list_output()]

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            index = len(commands)
            if index == 1:
                result = bind_failure_output()
                if leak_secret:
                    secret = cmd[cmd.index("--client-secret") + 1]
                    result = subprocess.CompletedProcess(
                        cmd, 1, b"",
                        BRIDGE_ERROR + secret.encode() + b"\n")
                if missing_diagnostic:
                    result = subprocess.CompletedProcess(
                        cmd, 1, b"", b'{"code":"bad_request"}')
                return result
            if index == 2:
                if phantom_self:
                    return list_output(items=[{
                        "id": "binding-x", "account_ref": runner.account}])
                if phantom is not None:
                    return list_output(items=phantom)
                return list_output()
            if index == 3:
                if group_drift:
                    return subprocess.CompletedProcess(
                        ["bcs-cli"], 0,
                        (f"Group created:\n  ID: {GROUP}\n"
                         f"  Driver: {DRIVER}\n"
                         f"  Participants: {MEMBER},{ENG}\n"
                         f"  Session: {INITIAL_SESSION}\n").encode(), b"")
                return group_output()
            if index == 4:
                if session_drift:
                    return subprocess.CompletedProcess(
                        ["bcs-cli"], 0, json.dumps({
                            "id": SESSION, "session_id": SESSION,
                            "group_id": "bcs_grp_other",
                        }).encode(), b"")
                return session_output()
            if index == 5:
                if conversation_items is not None:
                    return subprocess.CompletedProcess(
                        ["bcs-cli"], 0,
                        json.dumps({"items": conversation_items}).encode(),
                        b"")
                return conversation_output()
            if index == 6:
                return unbind_output()
            if index == 7:
                if after_drift:
                    return list_output(items=[{"id": "foreign-binding",
                                                "account_ref": "other"}])
                return list_output()
            return list_output()

        def fake_call(self_client, operation, **kwargs):
            if operation == CHANNEL_CLI_SESSION_DELETE_OPERATION:
                if session_delete_drift:
                    return ({"deleted": False}, deletion_record(
                        operation, "/sessions/" + kwargs["session_id"]))
                return ({"deleted": True,
                         "session_id": kwargs["session_id"]},
                        deletion_record(operation,
                                        "/sessions/" + kwargs["session_id"]))
            if operation == CHANNEL_CLI_GROUP_DELETE_OPERATION:
                if group_delete_drift:
                    return ({"deleted": False}, deletion_record(
                        operation, "/groups/" + kwargs["group_id"]))
                return ({"deleted": True, "id": kwargs["group_id"]},
                        deletion_record(operation,
                                        "/groups/" + kwargs["group_id"]))
            raise AssertionError("unexpected cleanup operation: " + operation)

        environment = {
            "BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
            "BCS_E2E_MEMBER_BOT_TOKEN": MEMBER_TOKEN,
            "BCS_E2E_ENG_BOT_TOKEN": ENG_TOKEN,
            "BCS_E2E_HUMAN_COOKIE": COOKIE,
        }
        with patch.dict(os.environ, environment), \
             patch("bcs_e2e.channel_cli_runner.capture",
                   side_effect=fake_capture), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=fake_call, autospec=True):
            adapter = RemoteChannelCliAdapter(profile, runner)
            execution = execute_channel_cli_case(adapter)
        return execution, adapter, commands, contexts

    def test_local_and_preprod_channel_cli_lifecycle_passes(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                result, adapter, commands, contexts = self.run_case(
                    preprod=preprod)
                self.assertEqual(result.status, "PASS")
                self.assertEqual(result.completed_count, 9)
                self.assertEqual(result.ready_to_execute, True)
                self.assertEqual(adapter.group_id, GROUP)
                self.assertEqual(adapter.initial_session_id, INITIAL_SESSION)
                self.assertEqual(adapter.session_id, SESSION)
                self.assertEqual(adapter.bindings_before, [])
                bind_command = commands[0]
                self.assertEqual(bind_command[1:5],
                                 ["--url",
                                  "https://teamclawgw.example.com" if preprod
                                  else "http://127.0.0.1:21100",
                                  "--json", "--log-level"])
                self.assertIn("--target-kind", bind_command)
                self.assertIn("bot", bind_command)
                self.assertIn(DRIVER, bind_command)
                self.assertIn("full_transcript", bind_command)
                self.assertIn("pre" if preprod else "local", bind_command)
                self.assertIn("e2e-robot", bind_command)
                self.assertIn("normal", bind_command)
                self.assertIn("markdown", bind_command)
                create_command = commands[2]
                self.assertEqual(create_command[-9:],
                                 ["create-group", "--driver", MEMBER,
                                  "--participants", MEMBER + "," + ENG,
                                  "--context",
                                  "Release coordination through bcs-cli",
                                  "--topic",
                                  "CLI channel conversation lookup"])
                self.assertEqual(commands[3][-6:],
                                 ["session", "create", "--group", GROUP,
                                  "--title", "CLI channel lookup"])
                self.assertEqual(commands[4][-4:],
                                 ["channel", "conversation-id",
                                  "--session", SESSION])
                self.assertEqual(commands[5][-3], "unbind")
                self.assertEqual(commands[5][-1],
                                 adapter.runner.missing)
                snapshot = result.snapshot()
                self.assertEqual(snapshot["steps"][0]["evidence"]["case_class"],
                                 "bind-redacted")
                self.assertEqual(snapshot["steps"][0]["data"],
                                 {"disabled_bridge": True,
                                  "secret_redacted": True})
                self.assertEqual(snapshot["steps"][1]["data"],
                                 {"items": 0, "phantom_free": True})
                self.assertEqual(snapshot["steps"][4]["data"],
                                 {"items": []})
                self.assertEqual(snapshot["steps"][5]["data"]["ok"], True)
                self.assertEqual(snapshot["steps"][6]["data"],
                                 {"items": 0, "phantom_free": True,
                                  "unchanged": True})
                self.assertEqual(snapshot["steps"][7]["evidence"]["transport"],
                                 "PRODUCT_HTTP")
                self.assertEqual(
                    snapshot["steps"][7]["evidence"]["caller_role"],
                    "case_owned_member")
                self.assertEqual(
                    snapshot["steps"][7]["evidence"]["credential_kind"],
                    "human_cookie" if preprod else "owned_local_bot_token")
                self.assertEqual(snapshot["steps"][7]["data"],
                                 {"deleted": True, "session_id": SESSION})
                self.assertEqual(snapshot["steps"][8]["data"],
                                 {"deleted": True, "id": GROUP})
                rendered = json.dumps(snapshot, sort_keys=True)
                self.assertNotIn(COOKIE, rendered)

    def test_bind_redaction_blocks_on_secret_leak(self):
        result, _, _, _ = self.run_case(leak_secret=True)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "ChannelCliRunnerError")
        self.assertEqual(result.completed_count, 0)

    def test_bind_redaction_blocks_on_missing_diagnostic(self):
        result, _, _, _ = self.run_case(missing_diagnostic=True)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "ChannelCliRunnerError")

    def test_phantom_account_binding_blocks_the_lifecycle(self):
        result, _, _, _ = self.run_case(phantom_self=True)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "ChannelCliRunnerError")
        self.assertEqual(result.completed_count, 1)

    def test_nonempty_conversation_lookup_blocks(self):
        result, _, _, _ = self.run_case(
            conversation_items=[{"conversation_id": "ding-1"}])
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.completed_count, 4)

    def test_group_driver_drift_blocks(self):
        result, _, _, _ = self.run_case(group_drift=True)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "ChannelCliRunnerError")

    def test_session_group_drift_blocks(self):
        result, _, _, _ = self.run_case(session_drift=True)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "ChannelCliRunnerError")

    def test_final_list_drift_blocks(self):
        result, _, _, _ = self.run_case(after_drift=True)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.completed_count, 6)

    def test_session_delete_drift_blocks(self):
        result, _, _, _ = self.run_case(session_delete_drift=True)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 7)

    def test_group_delete_drift_blocks(self):
        result, _, _, _ = self.run_case(group_delete_drift=True)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.completed_count, 8)

    def test_case_guard_accepts_the_channel_cli_case_files(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = [ROOT / "bcs_e2e/channel_cli_profile.py",
                 ROOT / "bcs_e2e/channel_cli_runner.py",
                 ROOT / "bcs_e2e/remote_channel_cli_adapter.py"]
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                 text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_profile_rejects_channel_route_drift(self):
        mutations = {
            "session-delete-route": lambda value: value["route_bindings"][
                CHANNEL_CLI_SESSION_DELETE_OPERATION].update(method="GET"),
            "group-delete-actor": lambda value: value["route_bindings"][
                CHANNEL_CLI_GROUP_DELETE_OPERATION].update(
                    path="/groups/{group_id}?bot_id={driver_actor_id}"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_channel_cli_profile(
                        channel_profile_value(mutate=mutate))

    def test_profile_rejects_missing_engine_actor(self):
        def drop_eng(value):
            value["actors"].pop("eng")
        with self.assertRaises(ConfigError):
            validate_channel_cli_profile(
                channel_profile_value(mutate=drop_eng))

    def test_plan_freezes_the_nine_obligations_in_order(self):
        plan = channel_cli_case_plan()
        self.assertEqual(plan["case_id"], CHANNEL_CLI_CASE_ID)
        self.assertEqual(plan["parent_id"],
                         "story_cli_operator_validates_channel_management")
        self.assertEqual(plan["requirement_ids"], [
            "CHANNEL-CLI.bind-redacted", "CHANNEL-CLI.list-before",
            "CHANNEL-CLI.group-create", "CHANNEL-CLI.session-create",
            "CHANNEL-CLI.conversation-empty", "CHANNEL-CLI.unbind-missing",
            "CHANNEL-CLI.list-after", "CHANNEL-CLI.session-delete",
            "CHANNEL-CLI.group-delete"])
        self.assertEqual(len(plan["adapter_methods"]), 9)
        self.assertEqual(len(set(plan["adapter_methods"])), 9)

    def test_adaptability_is_registered_and_does_not_grant_credit(self):
        value = channel_cli_adaptability()
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        row = next((row for row in rows
                    if row["case_id"] == value["case_id"]), None)
        self.assertIsNotNone(row)
        self.assertEqual(value["requirement_ids"], row["requirement_ids"])
        self.assertEqual(value["environments"], row["environments"])
        self.assertEqual(value["environments"],
                         ["singlebox-product-cli", "preprod-product-cli"])
        self.assertEqual(value["local_executor_status"], "LOCAL_FULL_EXECUTOR")
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])
        self.assertFalse(row["grants_release_credit"])
        self.assertIn("durable empty-SQLite-table", value["blocker"])

    def test_cli_operations_are_once_only_and_ordered(self):
        profile = validate_channel_cli_profile(channel_profile_value())
        runner = ChannelCliRunner(profile, binary=self.binary,
                                   binary_sha256=self.digest,
                                   state_dir=self.state)
        environment = {
            "BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
            "BCS_E2E_MEMBER_BOT_TOKEN": MEMBER_TOKEN,
            "BCS_E2E_ENG_BOT_TOKEN": ENG_TOKEN,
        }
        order = {"count": 0}

        def ordered_capture(cmd, **kwargs):
            order["count"] += 1
            if order["count"] == 1:
                return bind_failure_output()
            return list_output()

        with patch.dict(os.environ, environment), \
             patch("bcs_e2e.channel_cli_runner.capture",
                   side_effect=ordered_capture):
            with self.assertRaises(ChannelCliRunnerError):
                runner.execute("channel_cli.list_after")
            runner.execute("channel_cli.bind")
            result = runner.execute("channel_cli.list_before")
            self.assertEqual(result["data"], {"items": []})
            with self.assertRaises(ChannelCliRunnerError):
                runner.execute("channel_cli.list_before")
            with self.assertRaises(ChannelCliRunnerError):
                runner.execute("channel_cli.session_create")


if __name__ == "__main__":
    unittest.main()


class ChannelCliContextEnvTests(unittest.TestCase):
    """A1 guard: the cookie context carries the station environment selector."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = self.root / "state"
        self.state.mkdir()
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_human_cookie_context_injects_station_env(self):
        profile = validate_channel_cli_profile(channel_profile_value(preprod=True))
        runner = ChannelCliRunner(profile, binary=self.binary,
                                   binary_sha256=self.digest,
                                   state_dir=self.state)
        _, _, environment = runner._context("driver", "human_cookie", "human-cookie")
        self.assertEqual(environment["BCS_COOKIE"], "human-cookie")
        self.assertEqual(environment["tc_sdb_nenv"], "production")

    def test_owned_bot_context_keeps_station_env_out(self):
        profile = validate_channel_cli_profile(channel_profile_value(preprod=False))
        runner = ChannelCliRunner(profile, binary=self.binary,
                                   binary_sha256=self.digest,
                                   state_dir=self.state)
        _, session_file, environment = runner._context("driver", "owned_local_bot_token", "token")
        self.assertNotIn("tc_sdb_nenv", environment)
        self.assertEqual(json.loads(session_file.read_text(encoding="utf-8"))["token"], "token")
