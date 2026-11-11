"""Cross-environment collaboration-team CLI contract tests; no live request."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.collaboration_team_adapter import RemoteCollaborationTeamAdapter
from bcs_e2e.collaboration_team_contract import (
    CASE_ID,
    SUITE,
    collaboration_team_adaptability,
    execute_collaboration_team_case,
)
from bcs_e2e.collaboration_team_profile import (
    ConfigError,
    validate_collaboration_team_profile,
)
from bcs_e2e.collaboration_team_runner import CollaborationTeamRunner
from bcs_e2e.migration_inventory import migration_plan

ENG = "engineering-bot"
CS = "customer-service-bot"
PM = "product-manager-bot"
QA = "quality-bot"
COOKIE = "private-human-cookie-value"
DRIVER_TOKEN = "private-driver-token"


def profile_value(*, gateway=False):
    return {
        "schema_version": 1, "contract": "dual-collaboration-team-cli-profile-v1",
        "environment": {"tier": "preprod" if gateway else "local",
                        "id": "preprod" if gateway else "local",
                        "expected_bcs_env": "pre" if gateway else "local"},
        "entrypoint": {"kind": "gateway" if gateway else "singlebox",
                       "origin": "https://gateway.example.com" if gateway else "http://127.0.0.1:21000",
                       "api_face": "legacy", "manifest_face": "legacy"},
        "human": {"user_id": "owner-1"},
        "actors": {
            "engineering": {"bot_id": ENG, "owner_id": "owner-1", "dedicated": True,
                             "visibility": "public"},
            "customer_service": {"bot_id": CS, "owner_id": "owner-1", "dedicated": True,
                                 "visibility": "public"},
            "driver": {"bot_id": PM, "owner_id": "owner-1", "dedicated": True,
                        "visibility": "public"},
            "qa": {"bot_id": QA, "owner_id": "owner-1", "dedicated": True,
                    "visibility": "public"},
        },
        "auth": ({"kind": "human-cookie-with-owned-driver-bot-token",
                    "cookie_env": "BCS_E2E_HUMAN_COOKIE",
                    "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"} if gateway else
                 {"kind": "owned-local-bot-tokens",
                  "engineering_token_env": "BCS_E2E_ENG_BOT_TOKEN",
                  "customer_service_token_env": "BCS_E2E_CS_BOT_TOKEN",
                  "driver_token_env": "BCS_E2E_PM_BOT_TOKEN",
                  "qa_token_env": "BCS_E2E_QA_BOT_TOKEN"}),
        "relationships": {
            "visibility": "owned-cs-protect-then-public-restore",
            "friendship": "owned-eng-cs-request-then-reject",
            "group": "two-owned-pm-qa-groups",
            "cleanup": "none-runtime-or-operator-created",
            "retention": "request-and-group-history",
        },
        "policy": {"fixture_pool": "collaboration-team-pool",
                   "exclusive_use_confirmed": True, "timeout_seconds": 10,
                   "max_response_bytes": 65536,
                   "failure_retention_hours": 24},
        "cli": {"transport": "product-cli",
                 "command_family": "collaboration-team", "api_face": "legacy"},
    }


def group_object(group_id: str, status: str = "active",
                 participants=(("driver_bot", PM), ("engineering_bot", ENG))):
    rows = []
    for _role, bot in participants:
        rows.append({"bot_uuid": bot, "role": None})
    return {"id": group_id, "driver_bot": PM, "status": status,
            "participants": rows}


class CollaborationTeamContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.state = self.root / "state"
        self.state.mkdir()
        self.first_group = "cli_collab_group_1"
        self.second_group = "cli_collab_group_2"
        self.expanded_participants = (
            ("driver_bot", PM), ("engineering_bot", ENG), ("qa_bot", QA))
        self.formal_reads = []

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, *, gateway=False, outputs=None):
        profile = validate_collaboration_team_profile(profile_value(gateway=gateway))
        return CollaborationTeamRunner(profile, binary=self.binary,
                                          binary_sha256=self.digest,
                                          state_dir=self.state)

    def standard_output(self, operation):
        if operation == "visibility.protected":
            return {"success": True, "data": {"bot_uuid": CS, "visibility": "protected"}}
        if operation == "visibility.public":
            return {"success": True, "data": {"bot_uuid": CS, "visibility": "public"}}
        if operation == "friend.request":
            return {"success": True, "data": {
                "id": "request-1", "from_bot": ENG, "to_bot": CS,
                "status": "pending"}}
        if operation == "friend.requests.select":
            return [{"id": "request-1", "from_bot": ENG, "to_bot": CS,
                     "status": "pending"}]
        if operation == "friend.reject":
            return {"success": True}
        if operation == "friend.list":
            return [{"bot_uuid": "unrelated-bot"}]
        if operation == "group.create":
            return (f"Group created:\n  ID: {self.first_group}\n"
                     f"  Driver: {PM}\n  Participants: {PM},{ENG}\n"
                     f"  Session: {self.first_group}:session\n").encode()
        if operation == "termination.group.create":
            return (f"Group created:\n  ID: {self.second_group}\n"
                     f"  Driver: {PM}\n  Participants: {PM},{ENG}\n"
                     f"  Session: {self.second_group}:session\n").encode()
        if operation == "group.read.active":
            row = group_object(self.first_group)
            return json.dumps(row).encode()
        if operation == "group.read.after-member":
            row = group_object(self.first_group,
                               participants=self.expanded_participants)
            return json.dumps(row).encode()
        if operation == "group.add-member":
            return (f"✓ Member added to group:\n  Group: {self.first_group}\n"
                     f"  Bot: {QA}\n").encode()
        if operation == "group.list":
            page = { "returned": 1, "total": 1, "has_more": False,
                    "items": [group_object(self.first_group)]}
            return json.dumps(page).encode()
        if operation == "group.fuse":
            value = {"perspectives": [
                {"bot_uuid": PM, "summary": "release summary"},
                {"bot_uuid": ENG, "summary": "risk summary"},
                {"bot_uuid": QA, "summary": "qa summary"}],
                "conflicts": [], "recommendation": "Release is ready"}
            return b"Fusion result:\n" + json.dumps(value).encode()
        if operation == "group.status":
            return json.dumps({
                "updated": True, "group_id": self.first_group,
                "status": "completed", "changed_by": PM}).encode()
        if operation == "group.read.completed":
            return json.dumps(group_object(self.first_group, "completed")).encode()
        if operation == "group.terminate":
            return json.dumps({
                "terminated": True, "group_id": self.second_group,
                "status": "completed", "terminated_by": PM}).encode()
        if operation == "termination.group.read.completed":
            return json.dumps(group_object(self.second_group, "completed")).encode()
        raise AssertionError("unexpected operation " + operation)

    def run_case(self, *, mutate=None, gateway=False):
        self.formal_reads = []
        runner = self.runner(gateway=gateway)
        parsed_operations = []

        def operation_from_command(command):
            # Infer the product-CLI operation from the actual command, not from
            # the call index.  This keeps the mock order-sensitive.
            tail = tuple(command[6:])
            if tail == ("visibility", "set", "--value", "protected"):
                return "visibility.protected"
            if tail == ("visibility", "set", "--value", "public"):
                return "visibility.public"
            if tail == ("friend", "request", "--bot-uuid", CS):
                return "friend.request"
            if tail == ("friend", "requests", "--direction", "received",
                        "--status", "pending"):
                return "friend.requests.select"
            if (len(tail) == 4 and tail[:3] == ("friend", "reject", "--request-id")):
                return "friend.reject"
            if tail == ("friend", "list"):
                return "friend.list"
            if tail[0] == "create-group":
                return ("termination.group.create"
                        if tail[-1] == "CLI termination drill"
                        else "group.create")
            if tail[0] == "get-group":
                count = sum(name in {"group.read.active", "group.read.after-member",
                                     "group.read.completed",
                                     "termination.group.read.completed"}
                            for name in parsed_operations)
                return ("group.read.active", "group.read.after-member",
                        "group.read.completed", "termination.group.read.completed")[count]
            if (tail[0] == "add-member" and tail[1] == "--group"
                    and tail[3] == "--bot-uuid" and tail[4] == QA):
                return "group.add-member"
            if tail == ("list-groups", "--batch-size", "20"):
                return "group.list"
            if tail[0] == "fuse":
                return "group.fuse"
            if tail[0] == "group-status":
                return "group.status"
            if tail[0] == "terminate-group":
                return "group.terminate"
            raise AssertionError("unexpected product CLI command " + repr(tail))


        def fake_capture(command, **kwargs):
            operation = operation_from_command(command)
            parsed_operations.append(operation)
            raw = self.standard_output(operation)
            if not isinstance(raw, bytes):
                raw = json.dumps(raw).encode()
            if mutate is not None and operation == "group.fuse":
                raw = mutate(raw)
            return subprocess.CompletedProcess(command, 0, raw, b"")

        runner._formal_attempted = True
        def read_formal(expected_group_id):
            self.formal_reads.append(expected_group_id)
            return {
                "data": {"item_count": 1,
                         "requested_group_id": expected_group_id,
                         "requested_group_present": True},
                "evidence": {"transport": "PRODUCT_HTTP",
                              "operation": "collaboration.formal_group_list",
                              "caller_role": "driver",
                              "http_status": 200,
                              "credential_kind": "human_cookie" if gateway else "owned_local_bot_token",
                              "remote_validation": "NOT_RUN",
                              "grants_release_credit": False},
            }
        runner.read_formal_group_page = read_formal
        env = {
            "BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
            "BCS_E2E_ENG_BOT_TOKEN": "eng-token",
            "BCS_E2E_CS_BOT_TOKEN": "cs-token",
            "BCS_E2E_PM_BOT_TOKEN": DRIVER_TOKEN,
            "BCS_E2E_QA_BOT_TOKEN": "qa-token",
            "BCS_E2E_HUMAN_COOKIE": COOKIE,
        }
        with patch.dict("os.environ", env), \
             patch("bcs_e2e.collaboration_team_runner.capture",
                   side_effect=fake_capture):
            execution = execute_collaboration_team_case(
                RemoteCollaborationTeamAdapter(runner))
        return execution, runner, parsed_operations

    def test_case_guard_accepts_new_modules(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = (
            ROOT / "bcs_e2e/collaboration_team_profile.py",
            ROOT / "bcs_e2e/collaboration_team_runner.py",
            ROOT / "bcs_e2e/collaboration_team_adapter.py",
            ROOT / "bcs_e2e/collaboration_team_contract.py",
        )
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_adaptability_and_inventory_do_not_grant_credit(self):
        adaptability = collaboration_team_adaptability()
        self.assertEqual(adaptability["case_id"], CASE_ID)
        self.assertEqual(len(adaptability["requirement_ids"]), 8)
        self.assertEqual(adaptability["remote_validation"], "NOT_RUN")
        self.assertEqual(adaptability["local_executor_status"], "LOCAL_FULL_PARENT_EXECUTOR")
        self.assertFalse(adaptability["grants_release_credit"])
        self.assertEqual(adaptability["blockers"], [
            "Preprod product-CLI admission and execution remain NOT_RUN; "
            "remote_validation must stay NOT_RUN until explicitly authorized.",
            "Owned-local execution and unified Singlebox evidence receive no "
            "release credit for the full 29-Story release.",
        ])
        result = migration_plan()
        plan = result["cross_environment_product_cli_case_slices"]
        row = next(item for item in plan if item["case_id"] == CASE_ID)
        self.assertEqual(row["local_executor_status"], "LOCAL_FULL_PARENT_EXECUTOR")
        self.assertEqual(row["environments"], ["singlebox-product-cli", "preprod-product-cli"])
        local = next(item for item in result["local_registered_executors"]
                     if item["case_id"] == CASE_ID)
        self.assertEqual(local["suite"], SUITE)
        self.assertEqual(local["legacy_id"], "story_cli_operator_builds_collaboration_team")
        self.assertEqual(local["required_assertion_count"], 8)
        self.assertEqual(local["requirement_ids"], [
            "CLI-COLLAB-TEAM.protect-visibility",
            "CLI-COLLAB-TEAM.friend-request-reject",
            "CLI-COLLAB-TEAM.group-create-read",
            "CLI-COLLAB-TEAM.group-add-member",
            "CLI-COLLAB-TEAM.group-list",
            "CLI-COLLAB-TEAM.group-fuse",
            "CLI-COLLAB-TEAM.group-complete",
            "CLI-COLLAB-TEAM.group-terminate",
        ])
        self.assertEqual(local["execution_command"], "local-collaboration-team-run")
        self.assertEqual(local["report_command"], "local-collaboration-team-report")
        self.assertEqual(local["canonical_reporting"], "INTEGRATED_LOCAL")
        self.assertEqual(local["remote_validation"], "NOT_RUN")
        self.assertFalse(local["grants_release_credit"])
        self.assertEqual(result["migrated_story_count"], 0)

    def test_local_and_gateway_execute_one_reviewed_cli_sequence(self):
        for gateway in (False, True):
            with self.subTest(gateway=gateway):
                execution, runner, operations = self.run_case(gateway=gateway)
                self.assertEqual(execution.status, "PASS")
                self.assertEqual(execution.completed_count, 8)
                self.assertEqual(execution.total_count, 8)
                for step in execution.steps:
                    self.assertEqual(step.status, "PASS")
                self.assertEqual(operations, [
                    "visibility.protected", "friend.request",
                    "friend.requests.select", "friend.reject", "friend.list",
                    "visibility.public", "group.create", "group.read.active",
                    "group.add-member", "group.read.after-member", "group.list",
                    "group.fuse", "group.status", "group.read.completed",
                    "termination.group.create", "group.terminate",
                    "termination.group.read.completed"])
                self.assertEqual(self.formal_reads, [self.first_group])
                request_rows = {}
                for step in execution.steps:
                    rows = (step.evidence.get("requests")
                            if isinstance(step.evidence.get("requests"), list)
                            else [step.evidence])
                    for row in rows:
                        request_rows[row["case_operation"]] = row["credential_kind"]
                self.assertEqual(request_rows["group.status"],
                                 "human_cookie_with_owned_driver_bot_token" if gateway else "owned_local_bot_token")
                self.assertEqual(request_rows["group.terminate"],
                                 "human_cookie_with_owned_driver_bot_token" if gateway else "owned_local_bot_token")
                self.assertEqual(
                    request_rows["friend.request"],
                    "human_cookie" if gateway else "owned_local_bot_token")
                serialized = json.dumps(execution.snapshot(), sort_keys=True)
                for secret in (COOKIE, DRIVER_TOKEN, "eng-token", "cs-token", "qa-token"):
                    self.assertNotIn(secret, serialized)

    def test_preprod_driver_token_context_remains_private(self):
        profile = validate_collaboration_team_profile(profile_value(gateway=True))
        with patch.dict("os.environ", {"BCS_E2E_HUMAN_COOKIE": COOKIE}):
            root, context, environment = CollaborationTeamRunner._context(
                profile, profile["entrypoint"]["origin"], "driver",
                "human_cookie_with_owned_driver_bot_token", DRIVER_TOKEN, self.state)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        session = json.loads(context.read_text(encoding="utf-8"))
        self.assertEqual(session["token"], DRIVER_TOKEN)
        self.assertEqual(session["bot_uuid"], PM)
        self.assertEqual(environment["BCS_COOKIE"], COOKIE)
        self.assertEqual(environment["tc_sdb_nenv"], "production")

    def test_gateway_cookie_context_selects_non_oauth_transport(self):
        profile = validate_collaboration_team_profile(profile_value(gateway=True))
        root, context, environment = CollaborationTeamRunner._context(
            profile, profile["entrypoint"]["origin"], "customer_service",
            "human_cookie", COOKIE, self.state)
        try:
            self.assertEqual(environment["BCS_COOKIE"], COOKIE)
            self.assertEqual(environment["tc_sdb_nenv"], "production")
            session = json.loads(context.read_text(encoding="utf-8"))
            self.assertEqual(session["token"], "")
            self.assertEqual(session["bot_uuid"], CS)
            self.assertEqual(session["api_base_url"], profile["entrypoint"]["origin"])
        finally:
            import shutil
            shutil.rmtree(root)

    def test_friendship_and_group_readback_are_not_live_sample_order_dependent(self):
        # A real live run reordered expanded-group participants.  Equality must
        # remain a set comparison, while duplicate IDs still fail closed.
        self.expanded_participants = (
            ("engineering_bot", ENG), ("qa_bot", QA), ("driver_bot", PM))
        execution, _, operations = self.run_case()
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(operations[:6], [
            "visibility.protected", "friend.request", "friend.requests.select",
            "friend.reject", "friend.list", "visibility.public"])
        protect, friendship, _, expanded = execution.steps[:4]
        self.assertEqual(protect.data, {
            "customer_service_id": CS, "protected": True,
            "restored_public": False})
        self.assertEqual(friendship.data["request_id"], "request-1")
        self.assertEqual(friendship.data["requester"], ENG)
        self.assertEqual(friendship.data["target"], CS)
        self.assertTrue(friendship.data["rejection_acknowledged"])
        self.assertFalse(friendship.data["requester_final_list_contains_target"])
        self.assertTrue(friendship.data["restored_public"])
        self.assertEqual(set(expanded.data["participant_ids"]), {PM, ENG, QA})
        self.assertEqual(expanded.data["participant_ids"], [ENG, QA, PM])

        self.expanded_participants = (
            ("qa_bot", QA), ("driver_bot", PM), ("qa_bot", QA),
            ("engineering_bot", ENG))
        execution, _, _ = self.run_case()
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.steps[3].status, "NOT_RUN")
        self.assertEqual(execution.steps[3].error_type, "RunStateError")

    def test_inexact_fusion_blocks_the_whole_parent_story(self):
        raw = b"Fusion result:\n{}\n"
        execution, _, _ = self.run_case(
            mutate=lambda _original: raw)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 5)
        self.assertEqual(execution.steps[5].status, "NOT_RUN")

    def test_profile_rejects_unadmitted_actors_or_credentials(self):
        cases = {
            "wrong-cs-visibility": lambda profile: profile["actors"]["customer_service"].update(visibility="protected"),
            "cross-owner": lambda profile: profile["actors"]["qa"].update(owner_id="owner-2"),
            "local-cookie-auth": lambda profile: profile.update(auth={
                "kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}),
            "non-exclusive": lambda profile: profile["policy"].update(exclusive_use_confirmed=False),
            "wrong-relationship": lambda profile: profile["relationships"].update(cleanup="automatic"),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                value = profile_value()
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate_collaboration_team_profile(value)




class CollaborationTeamPreprodRoleTokenTests(unittest.TestCase):
    """Preprod owned per-role Bot token credential; dual-environment symmetry."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.state = self.root / "state"
        self.state.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def role_profile(self) -> dict:
        value = profile_value(gateway=True)
        value["auth"] = {
            "kind": "human-cookie-with-owned-role-bot-token",
            "cookie_env": "BCS_E2E_HUMAN_COOKIE",
            "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
            "engineering_token_env": "BCS_E2E_ENG_BOT_TOKEN",
            "customer_service_token_env": "BCS_E2E_CS_BOT_TOKEN",
        }
        return validate_collaboration_team_profile(value)

    def test_validator_admits_role_token_shape_and_rejects_drift(self):
        value = profile_value(gateway=True)
        value["auth"] = {
            "kind": "human-cookie-with-owned-role-bot-token",
            "cookie_env": "BCS_E2E_HUMAN_COOKIE",
            "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
            "engineering_token_env": "BCS_E2E_ENG_BOT_TOKEN",
            "customer_service_token_env": "BCS_E2E_CS_BOT_TOKEN",
        }
        validate_collaboration_team_profile(value)
        value["auth"]["qa_token_env"] = "BCS_E2E_QA_BOT_TOKEN"
        validate_collaboration_team_profile(value)
        value["auth"]["mystery_env"] = "BCS_E2E_X"
        with self.assertRaises(ConfigError):
            validate_collaboration_team_profile(value)
        value["auth"].pop("mystery_env")
        value["auth"].pop("qa_token_env")
        value["auth"].pop("engineering_token_env")
        value["auth"].pop("customer_service_token_env")
        with self.assertRaises(ConfigError):
            validate_collaboration_team_profile(value)

    def test_credential_resolves_owned_role_token_with_cookie_fallback(self):
        runner = CollaborationTeamRunner(
            self.role_profile(), binary=self.binary,
            binary_sha256=self.digest, state_dir=self.state)
        env = {"BCS_E2E_ENG_BOT_TOKEN": "eng-role-token",
               "BCS_E2E_CS_BOT_TOKEN": "cs-role-token",
               "BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict("os.environ", env):
            self.assertEqual(
                runner._credential("engineering", "visibility.protected"),
                ("human_cookie_with_owned_role_bot_token", "eng-role-token"))
            self.assertEqual(
                runner._credential("customer_service", "friend.requests.select"),
                ("human_cookie_with_owned_role_bot_token", "cs-role-token"))
            empty = dict(env)
            empty["BCS_E2E_ENG_BOT_TOKEN"] = ""
            with patch.dict("os.environ", empty):
                self.assertEqual(
                    runner._credential("engineering", "friend.list"),
                    ("human_cookie", COOKIE))
            self.assertEqual(
                runner._credential("driver", "group.status"),
                ("human_cookie_with_owned_driver_bot_token", DRIVER_TOKEN))
            self.assertEqual(
                runner._credential("driver", "group.add-member"),
                ("human_cookie_with_owned_role_bot_token", DRIVER_TOKEN))

    def test_role_context_carries_owned_token_and_cookie(self):
        profile = self.role_profile()
        with patch.dict("os.environ", {"BCS_E2E_HUMAN_COOKIE": COOKIE}):
            root, context, environment = CollaborationTeamRunner._context(
                profile, profile["entrypoint"]["origin"], "engineering",
                "human_cookie_with_owned_role_bot_token", "eng-role-token",
                self.state)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        session = json.loads(context.read_text(encoding="utf-8"))
        self.assertEqual(session["token"], "eng-role-token")
        self.assertEqual(session["bot_uuid"], ENG)
        self.assertEqual(session["api_base_url"],
                         profile["entrypoint"]["origin"])
        self.assertEqual(environment["BCS_COOKIE"], COOKIE)
        self.assertEqual(environment["tc_sdb_nenv"], "production")


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
