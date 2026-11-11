"""Cross-environment WORKSPACE incident core legacy-HTTP tests; no live request."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

TEST_ROOT = Path(__file__).resolve().parent
ROOT = TEST_ROOT.parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e.workspace_incident_core_contract import (
    CASE_ID,
    RemoteWorkspaceIncidentCoreAdapter,
    execute_workspace_incident_core_case,
    workspace_incident_core_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
    WORKSPACE_GROUP_CREATE_OPERATION,
    WORKSPACE_GROUP_DETAIL_OPERATION,
    WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION,
    WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION,
    validate_workspace_incident_core_profile,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.write_transport import WriteTransportError

DRIVER = "driver-bot"
MEMBER = "member-bot"
QA = "qa-bot"
GROUP = "bcs_grp_workspace_incident_core_01"
LABEL = "Incident response room"
DRIVER_TOKEN = "owned-driver-token"
QA_TOKEN = "owned-qa-token"
COOKIE = "human-cookie"


def profile_value(*, preprod=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-workspace-incident-core-profile-v1",
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": ("workspace-incident-core-preprod" if preprod
                   else "workspace-incident-core-local"),
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": {
            "kind": "gateway" if preprod else "singlebox",
            "origin": ("https://gateway.example.com" if preprod
                       else "http://127.0.0.1:21000"),
            "api_face": "legacy", "manifest_face": "legacy",
        },
        "human": {"user_id": "39464"},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": "39464",
                       "dedicated": True, "visibility": "public"},
            "member": {"bot_id": MEMBER, "owner_id": "39464",
                       "dedicated": True, "visibility": "public"},
            "qa": {"bot_id": QA, "owner_id": "39464",
                   "dedicated": True, "visibility": "public"},
        },
        "auth": {"kind": "incident-bundle",
                 "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
                 "qa_token_env": "BCS_E2E_QA_BOT_TOKEN",
                 "cookie_env": "BCS_E2E_HUMAN_COOKIE"},
        "relationships": {
            "incident_group": "bounded-owned-bots-incident-room",
            "participant_mode": "self-muted-by-owned-bot",
            "finalization": "exact-owned-group-delete-and-not-found",
        },
        "policy": {
            "fixture_pool": "workspace-incident-core-pool",
            "exclusive_use_confirmed": True, "timeout_seconds": 10,
            "max_response_bytes": 65536, "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-cli", "command_family": "group",
                "api_face": "legacy"},
        "route_bindings": {
            WORKSPACE_GROUP_CREATE_OPERATION: {
                "method": "POST", "path": "/groups",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_GROUP_DETAIL_OPERATION: {
                "method": "GET", "path": "/groups/{group_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION: {
                "method": "PUT",
                "path": "/groups/{group_id}/participants/{qa_actor_id}/mode",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/groups/{group_id}?bot_id={driver_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
        },
    }
    if mutate is not None:
        mutate(value)
    return value


LEGACY_METHODS = {
    WORKSPACE_GROUP_CREATE_OPERATION: "POST",
    WORKSPACE_GROUP_DETAIL_OPERATION: "GET",
    WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION: "PUT",
    WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION: "DELETE",
}
LEGACY_PATHS = {
    WORKSPACE_GROUP_CREATE_OPERATION: "/groups",
    WORKSPACE_GROUP_DETAIL_OPERATION: "/groups/{group_id}",
    WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION:
        "/groups/{group_id}/participants/{qa_actor_id}/mode",
    WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION:
        "/groups/{group_id}?bot_id={driver_actor_id}",
}


def created_incident_group():
    return {"id": GROUP, "driver_bot": DRIVER,
            "participants": [DRIVER, MEMBER, QA],
            "group_kind": "normal", "created": True,
            "context": None, "context_injected": False}


def participant_mode_ack():
    return {"success": True,
            "data": {"group_id": GROUP, "actor_id": QA,
                     "mode": "muted", "message_view_scope": "all"}}


def incident_delete_ack():
    return {"deleted": True, "id": GROUP}


def incident_detail(muted=False, roles=True):
    participants = [{"bot_uuid": DRIVER, "role": "driver"}]
    if roles:
        participants.append({"bot_uuid": MEMBER, "role": "consultant"})
        qa = {"bot_uuid": QA, "role": "consultant"}
        if muted:
            qa["mode"] = "muted"
        participants.append(qa)
    else:
        participants = [{"bot_uuid": "foreign-bot", "role": "driver"}]
    return {"id": GROUP, "label": LABEL, "status": "active",
            "context": None, "driver_bot": DRIVER,
            "participants": participants, "message_count": 0,
            "group_kind": "normal", "visibility": "public"}


def standard_responses():
    detail_calls = {"count": 0}

    def responses():
        return {
            WORKSPACE_GROUP_CREATE_OPERATION: created_incident_group(),
            WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION: (
                participant_mode_ack()),
            WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION: (
                incident_delete_ack()),
        }

    def fake_call(self_client, operation, **kwargs):
        data = responses().get(operation)
        if operation == WORKSPACE_GROUP_DETAIL_OPERATION:
            # call order: creation read-back, mute read-back,
            # post-delete not-found verification
            detail_calls["count"] += 1
            if detail_calls["count"] == 1:
                data = incident_detail()
            elif detail_calls["count"] == 2:
                data = incident_detail(muted=True)
            else:
                return None, {
                    "operation": operation,
                    "method": LEGACY_METHODS[operation],
                    "http_status": 404,
                    "path": LEGACY_PATHS[operation],
                    "submitted_request_id": "e2e-workspace-incident",
                    "traceparent_mode": "disabled",
                }
        if data is None:
            raise AssertionError(operation)
        return data, {
            "operation": operation,
            "method": LEGACY_METHODS[operation],
            "http_status": 200,
            "path": LEGACY_PATHS[operation],
            "submitted_request_id": "e2e-workspace-incident",
            "traceparent_mode": "disabled",
        }

    return fake_call, detail_calls


class WorkspaceIncidentCoreTests(unittest.TestCase):
    def run_case(self, *, preprod=False, deform=None):
        profile = profile_value(preprod=preprod)
        fake_call, _ = standard_responses()

        def deformed_call(self_client, operation, **kwargs):
            data, record = fake_call(self_client, operation, **kwargs)
            if deform is not None:
                deform(data, operation, kwargs)
            return data, record

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
               "BCS_E2E_QA_BOT_TOKEN": QA_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=deformed_call, autospec=True):
            adapter = RemoteWorkspaceIncidentCoreAdapter(profile)
            execution = execute_workspace_incident_core_case(adapter)
        return execution, adapter

    def test_local_and_preprod_incident_core(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                result, adapter = self.run_case(preprod=preprod)
                self.assertEqual(result.status, "PASS")
                self.assertEqual(result.completed_count, 3)
                self.assertEqual(adapter.group_id, GROUP)
                snapshot = result.snapshot()
                self.assertEqual(
                    [row["requirement_id"] for row in snapshot["steps"]],
                    ["WORKSPACE.incident-group",
                     "WORKSPACE.participant-mode",
                     "WORKSPACE.incident-delete"])
                self.assertEqual(
                    snapshot["steps"][0]["data"]["participants"],
                    [DRIVER, MEMBER, QA])
                self.assertEqual(
                    snapshot["steps"][0]["data"]["label"], LABEL)
                self.assertEqual(
                    snapshot["steps"][1]["data"]["actor_id"], QA)
                self.assertEqual(
                    snapshot["steps"][1]["data"]["mode"], "muted")
                self.assertEqual(
                    snapshot["steps"][2]["data"]["not_found_verified"],
                    True)
                serialized = json.dumps(snapshot, sort_keys=True)
                self.assertNotIn(COOKIE, serialized)
                self.assertNotIn(DRIVER_TOKEN, serialized)
                self.assertNotIn(QA_TOKEN, serialized)
                driver_kind = ("human_cookie_with_owned_driver_bot_token"
                               if preprod else "owned_local_bot_token")
                qa_kind = ("human_cookie_with_owned_qa_bot_token"
                           if preprod else "owned_local_bot_token")
                self.assertEqual(
                    snapshot["steps"][0]["evidence"]["credential_kind"],
                    driver_kind)
                self.assertEqual(
                    snapshot["steps"][1]["evidence"]["credential_kind"],
                    qa_kind)
                self.assertEqual(
                    snapshot["steps"][2]["evidence"]["credential_kind"],
                    "human_cookie" if preprod
                    else "owned_local_bot_token")
                self.assertEqual(
                    snapshot["steps"][0]["evidence"]["credential_kinds"],
                    [driver_kind, "human_cookie"] if preprod
                    else [driver_kind, driver_kind])
                self.assertEqual(
                    snapshot["steps"][1]["evidence"]["credential_kinds"],
                    [qa_kind, "human_cookie"] if preprod
                    else [qa_kind, qa_kind])
                self.assertEqual(
                    snapshot["steps"][2]["evidence"]["credential_kinds"],
                    ["human_cookie", "human_cookie"] if preprod
                    else ["owned_local_bot_token", "owned_local_bot_token"])

    def test_preprod_bot_subprofile_binds_the_reviewed_ace_duo(self):
        env = {"BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
               "BCS_E2E_QA_BOT_TOKEN": QA_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env):
            adapter = RemoteWorkspaceIncidentCoreAdapter(
                profile_value(preprod=True))
            driver_auth = adapter.driver_client.profile["auth"]
            qa_auth = adapter.qa_client.profile["auth"]
            human_auth = adapter.human_client.profile["auth"]
            local_adapter = RemoteWorkspaceIncidentCoreAdapter(
                profile_value(preprod=False))
            local_auth = local_adapter.driver_client.profile["auth"]
        self.assertEqual(driver_auth["kind"],
                         "human-cookie-with-owned-bot-token")
        self.assertEqual(driver_auth["header_env"],
                         {"Authorization": "BCS_E2E_DRIVER_BOT_TOKEN",
                          "Cookie": "BCS_E2E_HUMAN_COOKIE"})
        self.assertEqual(driver_auth["authorization_scheme"], "bearer")
        self.assertEqual(qa_auth["kind"],
                         "human-cookie-with-owned-bot-token")
        self.assertEqual(qa_auth["header_env"]["Authorization"],
                         "BCS_E2E_QA_BOT_TOKEN")
        self.assertEqual(qa_auth["authorization_scheme"], "bearer")
        self.assertEqual(human_auth["kind"], "human-cookie")
        self.assertEqual(human_auth["header_env"],
                         {"Cookie": "BCS_E2E_HUMAN_COOKIE"})
        self.assertNotIn("authorization_scheme", human_auth)
        self.assertEqual(local_auth, {
            "kind": "authenticated-product",
            "header_env": {"Authorization": "BCS_E2E_DRIVER_BOT_TOKEN"}})
        self.assertNotIn("authorization_scheme", local_auth)

    def test_create_roster_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_CREATE_OPERATION:
                data["participants"] = [DRIVER, MEMBER, QA,
                                        "foreign-bot"]
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 0)
        self.assertEqual(result.steps[0].status, "NOT_RUN")
        self.assertEqual(result.steps[1].status, "NOT_RUN")

    def test_create_acknowledgement_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_CREATE_OPERATION:
                data["created"] = False
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_detail_label_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if (operation == WORKSPACE_GROUP_DETAIL_OPERATION
                    and kwargs.get("group_id") == GROUP):
                data["label"] = "foreign-label"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_detail_roster_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if (operation == WORKSPACE_GROUP_DETAIL_OPERATION
                    and kwargs.get("group_id") == GROUP):
                data["participants"] = [{"bot_uuid": DRIVER,
                                         "role": "driver"},
                                        {"bot_uuid": MEMBER,
                                         "role": "consultant"}]
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_participant_mode_acknowledgement_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION:
                data["data"]["mode"] = "echoed"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 1)
        self.assertEqual(result.steps[1].status, "NOT_RUN")

    def test_self_mute_not_read_back_is_blocked(self):
        def deform(data, operation, kwargs):
            if (operation == WORKSPACE_GROUP_DETAIL_OPERATION
                    and kwargs.get("group_id") == GROUP):
                for row in data["participants"]:
                    row.pop("mode", None)
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 1)

    def test_delete_acknowledgement_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION:
                data["deleted"] = False
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 2)
        self.assertEqual(result.steps[2].status, "NOT_RUN")

    def test_incident_group_not_finalized_is_blocked(self):
        # the not-found verification reads a live Group instead of 404
        profile = profile_value()
        fake_call, _ = standard_responses()
        detail_calls = {"count": 0}

        def live_call(self_client, operation, **kwargs):
            if operation == WORKSPACE_GROUP_DETAIL_OPERATION:
                detail_calls["count"] += 1
                if detail_calls["count"] >= 3:
                    return incident_detail(), {
                        "operation": operation,
                        "method": "GET",
                        "http_status": 200,
                        "path": LEGACY_PATHS[operation],
                        "submitted_request_id": "e2e-workspace-incident",
                        "traceparent_mode": "disabled",
                    }
            return fake_call(self_client, operation, **kwargs)

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
               "BCS_E2E_QA_BOT_TOKEN": QA_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=live_call, autospec=True):
            adapter = RemoteWorkspaceIncidentCoreAdapter(profile)
            execution = execute_workspace_incident_core_case(adapter)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")
        self.assertEqual(execution.completed_count, 2)
        self.assertEqual(execution.steps[2].status, "NOT_RUN")

    def test_case_guard_accepts_the_case_files(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = [ROOT / "bcs_e2e/workspace_cli_profile.py",
                 ROOT / "bcs_e2e/workspace_incident_core_contract.py"]
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_profile_rejects_group_route_drift(self):
        mutations = {
            "create-method": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_CREATE_OPERATION].update(method="PUT"),
            "detail-path": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_DETAIL_OPERATION].update(
                    path="/openapi/v1/collaboration/groups/{group_id}"),
            "participant-mode-method": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION].update(
                    method="POST"),
            "participant-mode-placeholder": lambda value: value[
                "route_bindings"][
                WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION].update(
                    path="/groups/{group_id}/participants/"
                         "{member_actor_id}"),
            "delete-target": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION].update(
                    path="/groups/{group_id}?bot_id={qa_actor_id}"),
            "delete-method": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION].update(
                    method="POST"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_workspace_incident_core_profile(
                        profile_value(mutate=mutate))

    def test_profile_rejects_incident_contract_drift(self):
        mutations = {
            "auth-kind": lambda value: value["auth"].update(
                kind="human-cookie"),
            "missing-qa-token": lambda value: value["auth"].pop(
                "qa_token_env"),
            "missing-qa-actor": lambda value: value["actors"].pop("qa"),
            "effect-drift": lambda value: value["relationships"].update(
                incident_group="unreviewed-effect"),
            "family-drift": lambda value: value["cli"].update(
                command_family="session"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_workspace_incident_core_profile(
                        profile_value(mutate=mutate))

    def test_adaptability_is_registered_and_does_not_grant_credit(self):
        value = workspace_incident_core_adaptability()
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        row = next((row for row in rows
                    if row["case_id"] == value["case_id"]), None)
        self.assertIsNotNone(row)
        self.assertEqual(value["requirement_ids"], row["requirement_ids"])
        self.assertEqual(value["requirement_ids"],
                         ["WORKSPACE.incident-group",
                          "WORKSPACE.participant-mode",
                          "WORKSPACE.incident-delete"])
        self.assertEqual(row["environments"],
                         ["singlebox-product-cli", "preprod-product-cli"])
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])

    def test_failed_obligation_carries_error_detail(self):
        # Preprod tightened participant-mode authorization on 2026-11-11
        # (the bearer owner must equal the path actor), so a foreign QA
        # token surfaces as a WriteTransportError. The failing step and the
        # case snapshot must carry the transport detail instead of null.
        def reject_participant_mode(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION:
                raise WriteTransportError(
                    "operation workspace_group.participant_mode returned "
                    "HTTP 403")

        execution, _ = self.run_case(preprod=True,
                                     deform=reject_participant_mode)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 1)
        self.assertEqual(execution.error_type, "WriteTransportError")
        self.assertIn("returned HTTP 403", execution.error_detail or "")
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["error_type"], "WriteTransportError")
        self.assertIn("returned HTTP 403", snapshot["error_detail"] or "")
        steps = snapshot["steps"]
        self.assertEqual(steps[0]["status"], "PASS")
        self.assertIsNone(steps[0]["error_detail"])
        self.assertEqual(steps[1]["status"], "NOT_RUN")
        self.assertEqual(steps[1]["error_type"], "WriteTransportError")
        self.assertIn("returned HTTP 403", steps[1]["error_detail"] or "")
        self.assertEqual(steps[2]["status"], "NOT_RUN")
        self.assertIsNone(steps[2]["error_type"])
        self.assertIsNone(steps[2]["error_detail"])


if __name__ == "__main__":
    unittest.main()
