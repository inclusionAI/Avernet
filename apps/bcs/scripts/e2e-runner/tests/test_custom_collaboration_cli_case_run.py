"""Offline mock tests for the dual Custom-collaboration product-CLI case run.

Runner, cleanup WriteClient exits, the launcher envelope and the full frozen
leaf sequence are in-memory; credentials exist only as patched environment
values and are scanned for in every receipt and report.
"""
from __future__ import annotations
from contextlib import ExitStack, contextmanager, redirect_stdout
import hashlib, inspect, io, json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT)); sys.path.insert(0, str(ROOT))
from bcs_e2e import cli as bcs_cli
from bcs_e2e import custom_collaboration_cli_case_run as cc_run
from bcs_e2e import custom_collaboration_cli_leaf as cc_leaf
from bcs_e2e.case_adapters import AdapterCall
from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.cli import main
from bcs_e2e.custom_collaboration_cli_case_run import (
    CASE_COMMAND, CASE_KEYS, DUAL_CUSTOM_COLLAB_CASE_ID, DualCustomCollaborationAdapter, PARENT,
    REQUIREMENTS, _CASES, RemoteLocalCustomCollaborationAdapter, _require_step_evidence,
    custom_collaboration_cli_adaptability, custom_collaboration_cli_case_plan,
    dual_custom_collaboration_case_run, execute_custom_collaboration_case,
    load_custom_collaboration_case_profile)
from bcs_e2e.custom_collaboration_cli_leaf import (
    CREATE_PERSISTENT_GROUP_OPERATION, CUSTOM_COLLABORATION_CASE_METHODS, CUSTOM_COLLABORATION_CLI_EFFECT,
    CUSTOM_COLLABORATION_CLI_OPERATIONS, CUSTOM_COLLABORATION_CLI_PROFILE_CONTRACT, CUSTOM_COLLABORATION_CLI_RELATIONSHIPS,
    CUSTOM_COLLABORATION_CREATE_EFFECT, CUSTOM_COLLABORATION_REQUIREMENT_OPERATIONS, CUSTOM_COLLABORATION_TRIAL_EFFECT,
    CUSTOM_COLLABORATION_VALIDATE_EFFECT, DECLARED_CLI_EXIT_ROUTES, DEFINITION_YAML, GROUP_DELETE_OPERATION,
    GROUP_VERIFY_DELETED_OPERATION, LEAF_ARGUMENTS, READ_PERSISTENT_GROUP_OPERATION,
    RUN_CANCEL_OPERATION, SCENARIO_CLEANUP_OPERATIONS, SESSION_READ_OPERATION, TRIAL_ADD_MEMBER_OPERATION,
    TRIAL_CREATE_GROUP_OPERATION, TRIAL_MESSAGES_OPERATION, TRIAL_PERMISSION_OPERATION, TRIAL_QUERY_GRAPH_OPERATION,
    TRIAL_QUERY_PENDING_OPERATION, TRIAL_RESPOND_GUARD_OPERATION, TRIAL_START_RUN_OPERATION, VALIDATE_YAML_OPERATION,
    _HTTP_PRODUCT_OPERATIONS, _HTTP_SCENARIO_OPERATIONS,
    _STEP_EFFECTS, CustomCollaborationCliRunner, CustomCollaborationCliRunnerError,
    custom_collaboration_step_credential_kind, expected_step_transport, validate_custom_collaboration_cli_profile)
from bcs_e2e.local_cli import CUSTOM_COLLABORATION_CONTEXT, CUSTOM_COLLABORATION_TOPIC, TRIAL_CONTEXT, TRIAL_INPUT, TRIAL_TOPIC
from bcs_e2e.local_custom_collaboration_case import DENSITY_SPECS
from bcs_e2e.local_operator_cli_contract import CUSTOM_COLLABORATION_COMMANDS
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.run_state import RunStateError
from bcs_e2e.write_transport import WriteTransportError
RUN_ID = "custom-collab-run-1"
HUMAN = "39464"
DRIVER_BOT, MEMBER_BOT = "driver-custom-collab", "member-custom-collab"
PREPROD_DRIVER, PREPROD_MEMBER = "20261026_xq102n2b:39464", "20261010_gekng5hk:39464"
DRIVER_TOKEN_ENV, MEMBER_TOKEN_ENV, COOKIE_ENV = "BCS_E2E_DRIVER_BOT_TOKEN", "BCS_E2E_MEMBER_BOT_TOKEN", "BCS_E2E_HUMAN_COOKIE"
DRIVER_TOKEN, MEMBER_TOKEN, HUMAN_COOKIE = "secret-driver-token-value", "secret-member-token-value", "secret-human-cookie-value"
TRIAL_GROUP, TRIAL_SESSION = "grp-custom-trial-0001", "grp-custom-trial-0001:00000001"
PERSIST_GROUP, PERSIST_SESSION = "grp-custom-persistent-0002", "grp-custom-persistent-0002:00000001"
TRIAL_RUN = "run-custom-e2e-0001"
SECRETS = {DRIVER_TOKEN_ENV: DRIVER_TOKEN, MEMBER_TOKEN_ENV: MEMBER_TOKEN, COOKIE_ENV: HUMAN_COOKIE}


def local_auth():
    return {"kind": "owned-local-bot-tokens", "driver_token_env": DRIVER_TOKEN_ENV, "member_token_env": MEMBER_TOKEN_ENV}


def trio_auth():
    return {"kind": "human-cookie-with-owned-driver-bot-token", "driver_token_env": DRIVER_TOKEN_ENV, "cookie_env": COOKIE_ENV}


def cookie_auth():
    return {"kind": "human-cookie", "cookie_env": COOKIE_ENV}


def route_bindings():
    return {operation: {"method": method, "path": path, "api_face": "legacy", "auth": "case_owned_driver", "response": "legacy-json", "traceparent": "disabled"}
            for operation, (method, path) in DECLARED_CLI_EXIT_ROUTES.items()}


def profile_value(*, preprod=False, auth=None, mutate=None):
    driver, member = (PREPROD_DRIVER, PREPROD_MEMBER) if preprod else (DRIVER_BOT, MEMBER_BOT)
    value = {"schema_version": 1, "contract": CUSTOM_COLLABORATION_CLI_PROFILE_CONTRACT,
        "environment": {"tier": "preprod" if preprod else "local", "id": "custom-collaboration-cli-preprod" if preprod else "custom-collaboration-cli-local", "expected_bcs_env": "pre" if preprod else "local"},
        "entrypoint": {"kind": "gateway" if preprod else "singlebox", "origin": "https://bcs-pre.alipay.com" if preprod else "http://127.0.0.1:21000", "api_face": "legacy", "manifest_face": "legacy"},
        "human": {"user_id": HUMAN},
        "actors": {"driver": {"bot_id": driver, "owner_id": HUMAN, "dedicated": True, "visibility": "public"},
                   "member": {"bot_id": member, "owner_id": HUMAN, "dedicated": True, "visibility": "public"}},
        "auth": auth if auth is not None else (trio_auth() if preprod else local_auth()),
        "relationships": dict(CUSTOM_COLLABORATION_CLI_RELATIONSHIPS),
        "policy": {"fixture_pool": "custom-collaboration-cli-pool", "exclusive_use_confirmed": True, "timeout_seconds": 10, "max_response_bytes": 65536, "failure_retention_hours": 24},
        "cli": {"transport": "product-cli", "command_family": "collaboration", "api_face": "legacy"},
        "route_bindings": route_bindings()}
    if preprod:
        value["browser_origin"] = "https://teamclaw-pre.alipay.com"
    if mutate is not None:
        mutate(value)
    return value


def local_profile():
    return validate_custom_collaboration_cli_profile(profile_value())


def preprod_profile(auth=None):
    return validate_custom_collaboration_cli_profile(profile_value(preprod=True, auth=auth))


def completed(returncode, stdout, stderr=b""):
    return subprocess.CompletedProcess(["bcs-cli"], returncode, stdout, stderr)


def bytes_json(value):
    return json.dumps(value).encode("utf-8")


def cli_projection(profile, operation, **kwargs):
    driver, member = profile["actors"]["driver"]["bot_id"], profile["actors"]["member"]["bot_id"]
    human, session = "human_" + profile["human"]["user_id"], kwargs.get("session_id", TRIAL_SESSION)
    group = kwargs.get("group_id", TRIAL_GROUP)
    if operation == VALIDATE_YAML_OPERATION:
        return {"valid": True, "summary": {"participants": 2, "nodes": 2}}
    if operation == CREATE_PERSISTENT_GROUP_OPERATION:
        return {"id": PERSIST_GROUP, "session_id": PERSIST_SESSION, "driver_bot": driver, "participants": [driver, member], "created": True}
    if operation == READ_PERSISTENT_GROUP_OPERATION:
        return {"id": PERSIST_GROUP, "driver_bot": driver, "group_strategy": "state_machine", "participants": [{"bot_uuid": driver}, {"bot_uuid": member}]}
    if operation == TRIAL_CREATE_GROUP_OPERATION:
        return {"id": TRIAL_GROUP, "session_id": TRIAL_SESSION, "driver_bot": driver, "participants": [driver, member], "created": True}
    if operation == TRIAL_ADD_MEMBER_OPERATION:
        return {"session_id": session, "participants": [driver, member, {"bot_uuid": human, "role": "consultant"}]}
    if operation == TRIAL_PERMISSION_OPERATION:
        return {"allowed": True, "session_id": session, "group_id": group, "policy_version": "session_state_machine_v1", "group_strategy": "chat", "caller_bot_id": driver}
    if operation == TRIAL_START_RUN_OPERATION:
        return {"run": {"run_id": TRIAL_RUN, "session_id": session, "group_id": group, "status": "running"}}
    if operation == TRIAL_QUERY_GRAPH_OPERATION:
        return {"run": {"run_id": kwargs["run_id"], "session_id": session, "group_id": group}, "nodes": [{"node_id": "plan", "status": "completed"}, {"node_id": "review", "status": "running"}]}
    if operation == TRIAL_QUERY_PENDING_OPERATION:
        return []
    if operation == TRIAL_RESPOND_GUARD_OPERATION:
        return {"guard_refused": True, "node": "plan", "exit_code": 1, "not_pending": True}
    if operation == TRIAL_MESSAGES_OPERATION:
        return [{"view": "bcsPanel.StateMachineRunView", "run_id": kwargs["run_id"], "session_id": session}]
    raise AssertionError(operation)


class FakeChainRunner:
    def __init__(self, profile, *, binary=None, binary_sha256=None, state_dir=None):
        self.profile, self.state_dir, self.definition_path = profile, state_dir, Path("/owned-state/custom-collaboration-definition.yaml")
        self.calls, self.fail_at, self.invalid_at = [], None, None

    def execute(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation == self.fail_at:
            raise RuntimeError("simulated product CLI failure")
        if operation == self.invalid_at:
            return {"data": {"allowed": False, "participants": []}, "evidence": self.evidence(operation)}
        return {"data": cli_projection(self.profile, operation, **kwargs), "evidence": self.evidence(operation)}

    def evidence(self, operation):
        return {"transport": "PRODUCT_CLI", "operation": operation, "caller_role": LEAF_ARGUMENTS[operation][0], "command_arguments": [],
                "http_method_declared": "CLI", "route_declared": "product-cli",
                "credential_kind": custom_collaboration_step_credential_kind(self.profile, operation), "exit_status": 0, "elapsed_ms": 1.0}


class FakeWriteClient:
    instances, failed = [], False

    def __init__(self, profile, *, local_auth=None):
        self.profile, self.calls = profile, []
        FakeWriteClient.instances.append(self)

    def record(self, operation, http_status):
        return {"operation": operation, "http_status": http_status}

    def call(self, operation, *, group_id="", session_id="", body=None, expected=(200,), parse_error=False):
        self.calls.append((operation, dict(group_id=group_id, session_id=session_id, body=body, expected=expected, parse_error=parse_error)))
        if FakeWriteClient.failed:
            raise WriteTransportError("simulated cleanup transport failure")
        if operation == RUN_CANCEL_OPERATION:
            path = self.profile["route_bindings"][operation]["path"]
            run_id = path.split("/state-machine-runs/", 1)[1].rsplit("/cancel", 1)[0]
            return ({"run": {"run_id": run_id, "status": "aborted"}}, self.record(operation, 200))
        if operation == GROUP_DELETE_OPERATION:
            return ({"deleted": True, "id": group_id}, self.record(operation, 200))
        if operation == GROUP_VERIFY_DELETED_OPERATION:
            return ({"status": 404, "code": "group_not_found"}, self.record(operation, 404))
        return ({"session_id": session_id, "group_id": group_id, "status": "running"}, self.record(operation, 200))


class ExplodingRunner:
    def __init__(self, profile, *, binary, binary_sha256, state_dir):
        raise CustomCollaborationCliRunnerError("binary is not approved")


class ExplodingWriteClient:
    def __init__(self, profile, *, local_auth=None):
        raise WriteTransportError("simulated cleanup transport failure")


class FakeCapture:
    def __init__(self, *, returncode=0, stdout=b"", side_effect=None):
        self.calls, self.returncode, self.stdout, self.side_effect, self.staged_session = [], returncode, stdout, side_effect, None

    def __call__(self, command, *, timeout_seconds, max_output_bytes, environment=None, cwd=None, collect_stderr=False):
        self.calls.append({"command": list(command), "cwd": cwd, "timeout_seconds": timeout_seconds, "max_output_bytes": max_output_bytes, "environment": dict(environment or {})})
        session = Path(cwd) / "bot" / ".bcs" / "session.json"
        try:
            self.staged_session = json.loads(session.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.staged_session = None
        if self.side_effect is not None:
            raise self.side_effect
        payload = self.stdout
        if isinstance(payload, list):
            payload = payload.pop(0) if len(payload) > 1 else payload[0]
        return completed(self.returncode, payload)


class SequenceCapture:
    """Answer the full frozen leaf sequence by argv head."""

    def __init__(self, profile, *, guard_exit=1):
        self.driver, self.member = profile["actors"]["driver"]["bot_id"], profile["actors"]["member"]["bot_id"]
        self.human, self.guard_exit, self.calls = "human_" + profile["human"]["user_id"], guard_exit, []

    def __call__(self, command, *, timeout_seconds, max_output_bytes, environment=None, cwd=None, collect_stderr=False):
        self.calls.append({"command": list(command), "cwd": cwd, "environment": dict(environment or {})})
        argv, driver, member, human, session, group = command[6:], self.driver, self.member, self.human, TRIAL_SESSION, TRIAL_GROUP
        if argv[:2] == ["collaboration", "validate"]:
            return completed(0, bytes_json({"valid": True, "summary": {"participants": 2, "nodes": 2}}))
        if argv[:2] == ["collaboration", "create"]:
            context = argv[argv.index("--context") + 1]
            if context == TRIAL_CONTEXT:
                return completed(0, bytes_json({"id": TRIAL_GROUP, "session_id": TRIAL_SESSION, "driver_bot": driver, "participants": [driver, member], "created": True}))
            return completed(0, bytes_json({"id": PERSIST_GROUP, "session_id": PERSIST_SESSION, "driver_bot": driver, "participants": [driver, member], "created": True}))
        if argv[:2] == ["session", "add-member"]:
            return completed(0, bytes_json({"session_id": argv[2], "participants": [driver, member, {"bot_uuid": human, "role": "consultant"}]}))
        if argv[:2] == ["collaborate", "permission"]:
            return completed(0, bytes_json({"allowed": True, "session_id": argv[argv.index("--session") + 1], "group_id": group, "policy_version": "session_state_machine_v1", "group_strategy": "chat", "caller_bot_id": driver}))
        if argv[:2] == ["collaborate", "run"]:
            run_session = argv[argv.index("--session") + 1]
            return completed(0, bytes_json({"run": {"run_id": TRIAL_RUN, "session_id": run_session, "group_id": group, "status": "running"}}))
        if argv[:2] == ["collaborate", "query"] and "--graph" in argv:
            return completed(0, bytes_json({"run": {"run_id": TRIAL_RUN, "session_id": session, "group_id": group}, "nodes": [{"node_id": "plan", "status": "completed"}, {"node_id": "review", "status": "running"}]}))
        if argv[:2] == ["collaborate", "query"]:
            return completed(0, b"[]")
        if argv[:2] == ["collaborate", "respond"]:
            if self.guard_exit != 0:
                return completed(self.guard_exit, b"{}", stderr=b"guard refused: exact Human reply not pending")
            return completed(0, b'{"allowed": true}')
        if argv[:2] == ["session", "messages"]:
            return completed(0, bytes_json([{"view": "bcsPanel.StateMachineRunView", "run_id": TRIAL_RUN, "session_id": argv[2]}]))
        if argv[:1] == ["get-group"]:
            return completed(0, bytes_json({"id": PERSIST_GROUP, "driver_bot": driver, "group_strategy": "state_machine", "participants": [{"bot_uuid": driver}, {"bot_uuid": member}]}))
        raise AssertionError(f"unexpected argv {argv}")


def make_cli(root):
    binary = root / "bcs-cli"
    binary.write_bytes(b"#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


def fake_execution():
    step = StepOutcome(requirement_id=REQUIREMENTS[0], method="validate_yaml", status="PASS", data={"valid": True}, evidence={"credential_kind": "none"}, error_type=None)
    execution = CaseExecution(case_id=DUAL_CUSTOM_COLLAB_CASE_ID, parent_id=PARENT, status="PASS", steps=(step,), completed_count=1, total_count=1, error_type=None, ready_to_execute=True)
    execution.validate()
    return execution


def read_receipt(path):
    return json.loads(path.read_text(encoding="utf-8"))


class CustomCollaborationPlanAndRegistryTests(unittest.TestCase):
    def test_plan_registry_adaptability_and_signature_lock_the_frozen_shape(self):
        plan = custom_collaboration_cli_case_plan()
        self.assertEqual((plan["case_id"], plan["parent_id"], plan["requirement_ids"]), (DUAL_CUSTOM_COLLAB_CASE_ID, PARENT, list(REQUIREMENTS)))
        self.assertEqual(list(CUSTOM_COLLABORATION_REQUIREMENT_OPERATIONS), list(REQUIREMENTS))
        self.assertEqual(len(plan["adapter_methods"]), len(CUSTOM_COLLABORATION_CASE_METHODS))
        self.assertEqual(plan["cli_operations"], list(cc_leaf.CUSTOM_COLLABORATION_CLI_OPERATIONS))
        self.assertEqual(plan["cleanup_operations"], sorted(_HTTP_PRODUCT_OPERATIONS))
        self.assertEqual(plan["scenario_http_operations"], sorted(_HTTP_SCENARIO_OPERATIONS))
        self.assertEqual(plan["source_commands"], list(CUSTOM_COLLABORATION_COMMANDS))
        self.assertEqual(len(plan["source_expectations"]), 3)
        self.assertEqual((CASE_KEYS, CASE_COMMAND), (("custom-collaboration",), "dual-custom-collaboration-run"))
        self.assertEqual(set(_CASES), set(CASE_KEYS))
        item = _CASES["custom-collaboration"]
        self.assertEqual(item["case_id"], DUAL_CUSTOM_COLLAB_CASE_ID)
        self.assertIs(item["validator"], validate_custom_collaboration_cli_profile)
        self.assertIs(item["adapter"], DualCustomCollaborationAdapter)
        self.assertIs(item["execute"], execute_custom_collaboration_case)
        self.assertEqual(item["fixture_effect"], CUSTOM_COLLABORATION_CLI_EFFECT)
        parameters = inspect.signature(cc_run.dual_custom_collaboration_case_run).parameters
        self.assertEqual(list(parameters), ["profile_path", "cli_binary", "cli_binary_sha256", "state_dir", "run_id", "allow_write", "confirm_run_id"])
        self.assertTrue(all(mark.kind == inspect.Parameter.KEYWORD_ONLY for mark in parameters.values()))
        row = custom_collaboration_cli_adaptability()
        self.assertEqual((row["case_id"], row["legacy_id"], row["requirement_ids"]), (DUAL_CUSTOM_COLLAB_CASE_ID, PARENT, list(REQUIREMENTS)))
        self.assertEqual(row["environments"], ["singlebox-product-cli", "preprod-product-cli"])
        self.assertEqual((row["cli_surface"], row["local_executor_status"], row["remote_validation"], row["grants_release_credit"]), ("product-bcs-cli-collaboration", "LOCAL_FULL_PARENT_EXECUTOR", "NOT_RUN", False))
        self.assertIn("session-domain", row["blocker"])

    def test_report_is_registered_in_the_case_inventory(self):
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        if DUAL_CUSTOM_COLLAB_CASE_ID not in {row["case_id"] for row in rows}:
            self.skipTest("custom-collaboration inventory slice is wired by the lead")
        row = next(row for row in rows if row["case_id"] == DUAL_CUSTOM_COLLAB_CASE_ID)
        self.assertEqual((row["parent_id"], row["requirement_ids"]), (PARENT, list(REQUIREMENTS)))


class CustomCollaborationProfileValidatorTests(unittest.TestCase):
    def test_both_tier_forms_validate(self):
        local = validate_custom_collaboration_cli_profile(profile_value())
        self.assertEqual(local["environment"]["tier"], "local")
        self.assertNotIn("browser_origin", local)
        preprod = validate_custom_collaboration_cli_profile(profile_value(preprod=True))
        self.assertEqual(preprod["browser_origin"], "https://teamclaw-pre.alipay.com")
        self.assertEqual(validate_custom_collaboration_cli_profile(profile_value(preprod=True, auth=trio_auth()))["auth"]["kind"], "human-cookie-with-owned-driver-bot-token")
        self.assertEqual(validate_custom_collaboration_cli_profile(profile_value(preprod=True, auth=cookie_auth()))["auth"]["kind"], "human-cookie")
        alternate = profile_value(preprod=True, mutate=lambda v: v["actors"].update({"driver": {"bot_id": "20260101_zzz9zzz9:39464", "owner_id": HUMAN, "dedicated": True, "visibility": "public"}, "member": {"bot_id": "20260101_yyy8yyy8:39464", "owner_id": HUMAN, "dedicated": True, "visibility": "public"}}))
        self.assertTrue(validate_custom_collaboration_cli_profile(alternate))
        for preprod in (False, True):
            value = profile_value(preprod=preprod)
            value["actors"]["member"]["bot_id"] = value["actors"]["driver"]["bot_id"]
            with self.assertRaisesRegex(ConfigError, "distinct"):
                validate_custom_collaboration_cli_profile(value)

    def test_illegal_profiles_are_rejected(self):
        rows = (
            (False, "tier", lambda v: v["environment"].update({"tier": "staging"})),
            (True, "missing or unknown fields", lambda v: v.update({"extra": True})),
            (False, "missing or unknown fields", lambda v: v.update({"browser_origin": "https://teamclaw-pre.alipay.com"})),
            (True, "missing or unknown fields", lambda v: v.pop("browser_origin")),
            (False, "loopback", lambda v: v["entrypoint"].update({"origin": "http://bcs-pre.alipay.com"})),
            (True, "HTTPS required", lambda v: v["entrypoint"].update({"origin": "http://bcs-pre.alipay.com"})),
            (True, "gateway", lambda v: v["entrypoint"].update({"origin": "https://bcs.example.com"})),
            (True, "gateway", lambda v: v["entrypoint"].update({"kind": "singlebox"})),
            (False, "loopback", lambda v: v["entrypoint"].update({"kind": "gateway"})),
            (False, "loopback", lambda v: v["entrypoint"].update({"origin": "http://example.com"})),
            (False, "legacy face", lambda v: v["entrypoint"].update({"api_face": "public-v1"})),
            (True, "legacy face", lambda v: v["entrypoint"].update({"manifest_face": "internal-v1"})),
            (True, "expected_bcs_env", lambda v: v["environment"].update({"expected_bcs_env": "local"})),
            (False, "invalid identifier", lambda v: v["environment"].update({"id": "bad id"})),
            (False, "dedicated", lambda v: v["actors"]["driver"].update({"dedicated": False})),
            (True, "public dedicated", lambda v: v["actors"]["member"].update({"visibility": "private"})),
            (False, "owned by the Human", lambda v: v["actors"]["driver"].update({"owner_id": "stranger"})),
            (False, "owned Bot tokens", lambda v: v["auth"].update({"kind": "human-cookie"})),
            (False, "missing or unknown fields", lambda v: v["auth"].pop("member_token_env")),
            (True, "Human Cookie or the reviewed", lambda v: v["auth"].update({"kind": "human-cookie-with-owned-bot-tokens"})),
            (True, "Human Cookie or the reviewed", lambda v: v["auth"].pop("cookie_env")),
            (False, "environment reference", lambda v: v["auth"].update({"driver_token_env": "lowercase_env"})),
            (True, "environment reference", lambda v: v["auth"].update({"cookie_env": "lowercase_env"})),
            (False, "effect admission", lambda v: v["relationships"].update({"cleanup": "other"})),
            (True, "missing or unknown fields", lambda v: v["relationships"].pop("yaml_validation")),
            (False, "exclusive use", lambda v: v["policy"].update({"exclusive_use_confirmed": False})),
            (False, "timeout_seconds", lambda v: v["policy"].update({"timeout_seconds": 0})),
            (True, "max_response_bytes", lambda v: v["policy"].update({"max_response_bytes": 65537})),
            (False, "failure_retention_hours", lambda v: v["policy"].update({"failure_retention_hours": 169})),
            (True, "fixture_pool", lambda v: v["policy"].update({"fixture_pool": "bad pool"})),
            (False, "product CLI", lambda v: v["cli"].update({"command_family": "session"})),
            (False, "version or contract", lambda v: v.update({"schema_version": 2})),
            (True, "HTTPS required", lambda v: v.update({"browser_origin": "http://teamclaw-pre.alipay.com"})),
            (True, "pins teamclaw-pre.alipay.com", lambda v: v.update({"browser_origin": "https://bcs-pre.alipay.com"})),
            (True, "no credentials/path/query", lambda v: v.update({"browser_origin": "https://teamclaw-pre.alipay.com/chat"})),
            (True, "no credentials/path/query", lambda v: v.update({"browser_origin": "https://user:pw@teamclaw-pre.alipay.com"})),
        )
        for preprod, where, mutate in rows:
            with self.subTest(tier="preprod" if preprod else "local", where=where):
                with self.assertRaisesRegex(ConfigError, where):
                    validate_custom_collaboration_cli_profile(profile_value(preprod=preprod, mutate=mutate))

    def test_route_bindings_must_declare_the_four_reviewed_cleanup_exits(self):
        rows = (
            (lambda value: value["route_bindings"].pop(RUN_CANCEL_OPERATION), "four reviewed cleanup exits and the converged group-domain Run start"),
            (lambda value: value["route_bindings"].update({"custom_collaboration.unknown.op": dict(route_bindings()[RUN_CANCEL_OPERATION])}), "four reviewed cleanup exits and the converged group-domain Run start"),
            (lambda value: value["route_bindings"][SESSION_READ_OPERATION].update({"method": "POST"}), "product contract"),
            (lambda value: value["route_bindings"][GROUP_DELETE_OPERATION].update({"path": "/groups/{group_id}"}), "product contract"),
            (lambda value: value["route_bindings"][GROUP_VERIFY_DELETED_OPERATION].update({"auth": "case_owned_member"}), "product contract"),
            (lambda value: value["route_bindings"][RUN_CANCEL_OPERATION].update({"traceparent": "enabled"}), "product contract"),
        )
        for mutate, where in rows:
            with self.subTest(where=where):
                with self.assertRaisesRegex(ConfigError, where):
                    validate_custom_collaboration_cli_profile(profile_value(preprod=True, mutate=mutate))

    def test_credential_and_transport_labels_are_aware(self):
        for tier_profile, label in ((local_profile(), "owned_local_bot_token"), (preprod_profile(trio_auth()), "human_cookie_with_owned_driver_bot_token"), (preprod_profile(cookie_auth()), "human_cookie")):
            self.assertEqual(cc_leaf.custom_collaboration_cli_credential_kind(tier_profile), label)
        self.assertTrue(all(expected_step_transport(op) == "PRODUCT_CLI" for op in CUSTOM_COLLABORATION_CLI_OPERATIONS))
        self.assertTrue(all(expected_step_transport(op) == "PRODUCT_HTTP" for op in _HTTP_PRODUCT_OPERATIONS | _HTTP_SCENARIO_OPERATIONS))
        for tier_profile in (local_profile(), preprod_profile(trio_auth()), preprod_profile(cookie_auth())):
            for operation in (VALIDATE_YAML_OPERATION, TRIAL_START_RUN_OPERATION, RUN_CANCEL_OPERATION):
                if operation == VALIDATE_YAML_OPERATION:
                    expected = ("none"
                                if tier_profile["environment"]["tier"] == "local"
                                else cc_leaf.custom_collaboration_cli_credential_kind(tier_profile))
                elif operation in _HTTP_PRODUCT_OPERATIONS or operation in _HTTP_SCENARIO_OPERATIONS:
                    expected = "owned_local_bot_token" if tier_profile["environment"]["tier"] == "local" else "human_cookie"
                else:
                    expected = cc_leaf.custom_collaboration_cli_credential_kind(tier_profile)
                self.assertEqual(custom_collaboration_step_credential_kind(tier_profile, operation), expected)


class CustomCollaborationExecutionTests(unittest.TestCase):
    def setUp(self):
        FakeWriteClient.instances.clear()
        FakeWriteClient.failed = False

    def chain(self, *, preprod=False, auth=None, fail_at=None, invalid_at=None):
        profile = preprod_profile(auth) if preprod else local_profile()
        runner = FakeChainRunner(profile)
        runner.fail_at, runner.invalid_at = fail_at, invalid_at
        with patch.object(cc_run, "WriteClient", FakeWriteClient):
            adapter = DualCustomCollaborationAdapter(profile, runner)
            execution = execute_custom_collaboration_case(adapter)
        return adapter, runner, execution

    def test_dual_pass_collects_ordered_evidence_and_step_data(self):
        adapter, runner, execution = self.chain()
        self.assertEqual((execution.status, execution.completed_count, execution.total_count, execution.error_type), ("PASS", 37, 37, None))
        self.assertEqual([step.requirement_id for step in execution.steps],
                         [REQUIREMENTS[0], REQUIREMENTS[2], REQUIREMENTS[1]]
                         + ["CUSTOM-COLLABORATION." + name for name, _m, _f, _l in DENSITY_SPECS])
        self.assertEqual([step.method for step in execution.steps], ["validate_yaml", "trial_one_shot_lifecycle", "create_persistent_group"]
                         + [method for _name, method, _f, _l in DENSITY_SPECS])
        cli_order = [operation for operation, _ in CUSTOM_COLLABORATION_CASE_METHODS
                     if operation not in _HTTP_PRODUCT_OPERATIONS | _HTTP_SCENARIO_OPERATIONS]
        self.assertEqual([call[0] for call in runner.calls], cli_order)
        validate, trial, create = execution.steps[:3]
        self.assertEqual(validate.data, {"valid": True, "summary": {"participants": 2, "nodes": 2}})
        self.assertEqual(trial.data, {
            "trial_group_id": TRIAL_GROUP, "trial_group_strategy": "state_machine",
            "trial_group_session_handle": TRIAL_SESSION, "trial_session_id": TRIAL_SESSION,
            "trial_session_origin": "create_bound_consultant_session",
            "trial_run_id": TRIAL_RUN, "run_start_transport": "product_cli_session_domain",
            "session_domain_leaves": ["trial_add_member", "trial_permission",
                                      "trial_start_run"],
            "pending_human_inputs": 0, "respond_guard": "REFUSED", "restored_history": True,
            "run_cancelled": True, "session_preserved_running": True, "group_cleanup": "DELETED",
            "group_delete_verified_404": True,
            "converged_transport": (
                "persistent-form trial Group via the reviewed create leaf; Human "
                "consultant membership, trial authority and the one-shot Run start "
                "ride the upstream-anchored session-domain product-CLI leaves; "
                "run-domain CLI reads, respond guard, cancel and exact delete follow")})
        self.assertEqual(create.data, {"group_id": PERSIST_GROUP, "readback_group_strategy": "state_machine", "group_cleanup": "DELETED", "group_delete_verified_404": True})
        self.assertEqual(adapter.persistent_group_id, PERSIST_GROUP)
        records = [record for step in execution.steps for record in step.evidence["requests"]]
        self.assertEqual(len(records), len(CUSTOM_COLLABORATION_CASE_METHODS) + len(DENSITY_SPECS))
        for (operation, _method), record in zip(CUSTOM_COLLABORATION_CASE_METHODS, records):
            self.assertEqual(record["transport"], expected_step_transport(operation))
            self.assertTrue(record["caller_role"])
            self.assertEqual(record["credential_kind"], custom_collaboration_step_credential_kind(adapter.profile, operation))
            self.assertEqual((record["remote_validation"], record["grants_release_credit"]), ("NOT_RUN", False))
            self.assertTrue(record["case_class"])
            if operation in SCENARIO_CLEANUP_OPERATIONS:
                persistent = record["case_class"].startswith("persistent")
                self.assertEqual(record["fixture_effect"], CUSTOM_COLLABORATION_CREATE_EFFECT if persistent else CUSTOM_COLLABORATION_TRIAL_EFFECT)
            else:
                self.assertEqual(record["fixture_effect"], _STEP_EFFECTS[operation])

    def test_cancel_exit_derives_the_per_run_cleanup_profile(self):
        adapter, _, execution = self.chain()
        self.assertEqual(execution.status, "PASS")
        cancel = FakeWriteClient.instances[-1]
        binding = cancel.profile["route_bindings"][RUN_CANCEL_OPERATION]
        self.assertEqual((binding["method"], binding["api_face"], binding["traceparent"]), ("POST", "legacy", "disabled"))
        self.assertEqual(binding["path"], f"/state-machine-runs/{quote(TRIAL_RUN, safe='')}/cancel")
        self.assertEqual((cancel.calls[0][0], cancel.calls[0][1]["body"]), (RUN_CANCEL_OPERATION, {"reason": "E2E one-shot trial completed"}))

    def test_trial_session_bindings_pin_the_create_bound_session(self):
        adapter, runner, execution = self.chain()
        self.assertEqual(execution.status, "PASS")
        # S14 session-domain convergence: the create-bound consultant Session
        # carries every session-domain leaf (membership, authority, one-shot
        # Run start, restored history); the raw group-domain HTTP Run start is
        # a declared fallback route binding and is never executed, so no
        # run-scoped derived Session is projected.
        self.assertFalse(hasattr(adapter, "trial_run_session_id"))
        self.assertEqual((adapter.trial_session_id, adapter.trial_group_session_handle),
                         (TRIAL_SESSION, TRIAL_SESSION))
        seen = {}
        for operation, kwargs in FakeWriteClient.instances[0].calls:
            seen.setdefault(operation, []).append(kwargs)
        self.assertEqual(seen[SESSION_READ_OPERATION][0]["session_id"], TRIAL_SESSION)
        cli_kwargs = {operation: kwargs for operation, kwargs in runner.calls}
        self.assertEqual(cli_kwargs[TRIAL_ADD_MEMBER_OPERATION]["session_id"], TRIAL_SESSION)
        self.assertEqual(cli_kwargs[TRIAL_ADD_MEMBER_OPERATION]["human_id"], "human_" + HUMAN)
        self.assertEqual(cli_kwargs[TRIAL_PERMISSION_OPERATION]["session_id"], TRIAL_SESSION)
        self.assertEqual(cli_kwargs[TRIAL_START_RUN_OPERATION]["session_id"], TRIAL_SESSION)
        self.assertEqual(cli_kwargs[TRIAL_MESSAGES_OPERATION]["session_id"], TRIAL_SESSION)
        self.assertEqual(cli_kwargs[TRIAL_QUERY_GRAPH_OPERATION]["run_id"], TRIAL_RUN)

    def test_failed_leaf_or_drifted_projection_blocks_without_release_credit(self):
        for fail_at, invalid_at in ((TRIAL_CREATE_GROUP_OPERATION, None), (None, TRIAL_MESSAGES_OPERATION)):
            with self.subTest(fail_at=fail_at, invalid_at=invalid_at):
                _, _, execution = self.chain(fail_at=fail_at, invalid_at=invalid_at)
                self.assertEqual((execution.status, execution.completed_count, execution.error_type), ("BLOCKED", 0, "RunStateError"))
                for step in execution.steps:
                    self.assertEqual((step.status, step.error_type, step.data, step.evidence), ("NOT_RUN", "RunStateError", None, {}))

    def test_failure_classes_are_projected_onto_blocked_steps(self):
        # A failing CLI leaf, a cleanup transport failure and a drifted
        # projection each convert to a distinct scrubbed public class; every
        # frozen step carries the class in error_gap, replacing the r1-r4
        # all-first-step-NOT_RUN black box.
        _, _, execution = self.chain(fail_at=TRIAL_CREATE_GROUP_OPERATION, invalid_at=None)
        self.assertEqual(execution.error_type, "RunStateError")
        for step in execution.steps:
            self.assertEqual(step.error_gap, "CASE_CONTRACT_VIOLATION")
        FakeWriteClient.failed = True
        try:
            _, _, transport = self.chain()
        finally:
            FakeWriteClient.failed = False
        self.assertEqual(transport.error_type, "WriteTransportError")
        for step in transport.steps:
            self.assertEqual(step.error_gap, "TRANSPORT_HTTP_ERROR")

    def test_base_adapter_family_is_not_dual_admitted(self):
        self.assertTrue(issubclass(DualCustomCollaborationAdapter, RemoteLocalCustomCollaborationAdapter))
        profile = local_profile()
        with patch.object(cc_run, "WriteClient", FakeWriteClient):
            base = RemoteLocalCustomCollaborationAdapter(profile, FakeChainRunner(profile))
            self.assertIsInstance(DualCustomCollaborationAdapter(profile, FakeChainRunner(profile)), RemoteLocalCustomCollaborationAdapter)
            with self.assertRaisesRegex(RunStateError, "dual adapter"):
                execute_custom_collaboration_case(base)

    def test_step_evidence_gates_reject_drift_and_segment_cleanup_effects(self):
        adapter, _, _ = self.chain()

        def record(**overrides):
            base = {"transport": "PRODUCT_CLI", "caller_role": "operator", "credential_kind": "none", "remote_validation": "NOT_RUN", "grants_release_credit": False, "fixture_effect": CUSTOM_COLLABORATION_VALIDATE_EFFECT, "case_class": "validate-yaml"}
            base.update(overrides)
            return AdapterCall(data={}, evidence=base)

        good = _require_step_evidence(adapter, record(), VALIDATE_YAML_OPERATION)
        self.assertEqual(good["transport"], "PRODUCT_CLI")
        for operation, case_class, effect, transport, role in ((RUN_CANCEL_OPERATION, "cleanup-cancel", CUSTOM_COLLABORATION_TRIAL_EFFECT, "PRODUCT_HTTP", "case_owned_driver"), (TRIAL_START_RUN_OPERATION, "trial-run-start", CUSTOM_COLLABORATION_TRIAL_EFFECT, "PRODUCT_CLI", "driver"), (GROUP_DELETE_OPERATION, "persistent-cleanup-delete", CUSTOM_COLLABORATION_CREATE_EFFECT, "PRODUCT_HTTP", "case_owned_driver"), (GROUP_VERIFY_DELETED_OPERATION, "cleanup-verify-deleted", CUSTOM_COLLABORATION_TRIAL_EFFECT, "PRODUCT_HTTP", "case_owned_driver")):
            accepted = _require_step_evidence(adapter, record(transport=transport, caller_role=role, credential_kind=custom_collaboration_step_credential_kind(adapter.profile, operation), case_class=case_class, fixture_effect=effect), operation)
            self.assertEqual(accepted["fixture_effect"], effect)
        for overrides in ({"transport": "PRODUCT_HTTP"}, {"caller_role": ""}, {"credential_kind": "human_cookie"}, {"remote_validation": "CLOSED"}, {"grants_release_credit": True}, {"fixture_effect": "OTHER_EFFECT"}):
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(RunStateError, "evidence"):
                    _require_step_evidence(adapter, record(**overrides), VALIDATE_YAML_OPERATION)
        for case_class, effect in (("persistent-cleanup-delete", CUSTOM_COLLABORATION_TRIAL_EFFECT), ("cleanup-delete", CUSTOM_COLLABORATION_CREATE_EFFECT)):
            with self.subTest(case_class=case_class):
                with self.assertRaisesRegex(RunStateError, "evidence"):
                    _require_step_evidence(adapter, record(transport="PRODUCT_HTTP", caller_role="case_owned_driver", credential_kind="owned_local_bot_token", case_class=case_class, fixture_effect=effect), GROUP_DELETE_OPERATION)
        with self.assertRaisesRegex(RunStateError, "AdapterCall"):
            _require_step_evidence(adapter, {"evidence": {}}, VALIDATE_YAML_OPERATION)

    def test_http_cleanup_exits_bind_identity_routes_and_exact_groups(self):
        adapter, _, execution = self.chain()
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(adapter.cleanup_client.profile["auth"], {"kind": "authenticated-product", "header_env": {"Authorization": DRIVER_TOKEN_ENV}})
        self.assertEqual(adapter.cleanup_client.profile["entrypoint"], {"kind": "singlebox", "origin": "http://127.0.0.1:21000", "api_face": "legacy", "manifest_face": "legacy"})
        seen = {}
        for operation, kwargs in FakeWriteClient.instances[0].calls:
            seen.setdefault(operation, []).append(kwargs)
        self.assertEqual([operation for operation, _ in FakeWriteClient.instances[0].calls], [SESSION_READ_OPERATION, GROUP_DELETE_OPERATION, GROUP_VERIFY_DELETED_OPERATION, GROUP_DELETE_OPERATION, GROUP_VERIFY_DELETED_OPERATION])
        self.assertEqual(seen[SESSION_READ_OPERATION][0]["session_id"], TRIAL_SESSION)
        self.assertEqual(seen[SESSION_READ_OPERATION][0]["group_id"], TRIAL_GROUP)
        self.assertEqual([kwargs["group_id"] for kwargs in seen[GROUP_DELETE_OPERATION]], [TRIAL_GROUP, PERSIST_GROUP])
        self.assertEqual([kwargs["group_id"] for kwargs in seen[GROUP_VERIFY_DELETED_OPERATION]], [TRIAL_GROUP, PERSIST_GROUP])
        for kwargs in seen[GROUP_DELETE_OPERATION]:
            self.assertEqual(kwargs["expected"], (200,))
        for kwargs in seen[GROUP_VERIFY_DELETED_OPERATION]:
            self.assertEqual((kwargs["expected"], kwargs["parse_error"]), ((404,), True))

    def test_preprod_cleanup_exits_are_cookie_routed_and_driver_scoped(self):
        adapter, _, execution = self.chain(preprod=True, auth=trio_auth())
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(adapter.cleanup_client.profile["auth"], {"kind": "human-cookie", "header_env": {"Cookie": COOKIE_ENV}})
        records = [record for step in execution.steps for record in step.evidence["requests"]]
        for record in records:
            if record["transport"] == "PRODUCT_CLI":
                # remote validation presents the family credential: the
                # station gateway identity-gates the validation route live
                expected = "human_cookie_with_owned_driver_bot_token"
            else:
                expected = "human_cookie"
                self.assertEqual(record["caller_role"], "case_owned_driver")
            self.assertEqual(record["credential_kind"], expected)


class CustomCollaborationLeafRunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.cli_binary, self.cli_sha256 = make_cli(self.root)
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / cc_run.DEFINITION_FILE).write_text(DEFINITION_YAML, encoding="utf-8")
        self.launch_base = self.root / "launcher"
        self.base_patcher = patch.object(CustomCollaborationCliRunner, "_LAUNCHER_ROOT_BASE", self.launch_base)
        self.base_patcher.start()
        self.addCleanup(self.base_patcher.stop)

    def fresh(self, profile=None):
        return CustomCollaborationCliRunner(profile or local_profile(), binary=self.cli_binary, binary_sha256=self.cli_sha256, state_dir=self.state)

    @contextmanager
    def launcher(self, *, returncode=0, stdout=b"", side_effect=None):
        capture = FakeCapture(returncode=returncode, stdout=stdout, side_effect=side_effect)

        def fake_which(name):
            return "/usr/local/bin/docker" if name == "docker" else None

        with patch.object(cc_leaf.shutil, "which", fake_which), patch.object(cc_leaf, "capture", capture), patch.dict(os.environ, {"DOCKER_HOST": "unix:///kept/docker.sock"}):
            yield capture

    def kwargs_for(self, operation):
        values = {"yaml_path": self.state / cc_run.DEFINITION_FILE, "group_id": PERSIST_GROUP, "session_id": TRIAL_SESSION, "run_id": TRIAL_RUN, "human_id": "human_" + HUMAN}
        return {name: values[name] for name in LEAF_ARGUMENTS[operation][2]}

    def test_reviewed_leaf_argvs_freeze_the_owned_parent_forms(self):
        runner, yaml = self.fresh(), str(self.state / cc_run.DEFINITION_FILE)
        rows = {
            VALIDATE_YAML_OPERATION: ("operator", ("collaboration", "validate", yaml)),
            TRIAL_CREATE_GROUP_OPERATION: ("driver", ("collaboration", "create", yaml, "--driver", DRIVER_BOT, "--binding", f"planner={DRIVER_BOT}", "--binding", f"reviewer={MEMBER_BOT}", "--context", TRIAL_CONTEXT, "--topic", TRIAL_TOPIC)),
            TRIAL_ADD_MEMBER_OPERATION: ("driver", ("session", "add-member", TRIAL_SESSION, "--bot-uuid", "human_" + HUMAN, "--role", "consultant")),
            TRIAL_PERMISSION_OPERATION: ("driver", ("collaborate", "permission", "--session", TRIAL_SESSION)),
            TRIAL_START_RUN_OPERATION: ("driver", ("collaborate", "run", yaml, "--session", TRIAL_SESSION, "--binding", f"planner={DRIVER_BOT}", "--binding", f"reviewer={MEMBER_BOT}", "--input", TRIAL_INPUT)),
            TRIAL_QUERY_GRAPH_OPERATION: ("driver", ("collaborate", "query", "--run", TRIAL_RUN, "--graph")),
            TRIAL_QUERY_PENDING_OPERATION: ("driver", ("collaborate", "query", "--run", TRIAL_RUN, "--pending")),
            TRIAL_RESPOND_GUARD_OPERATION: ("driver", ("collaborate", "respond", "--run", TRIAL_RUN, "--node", "plan", "--content", "invalid Human reply")),
            TRIAL_MESSAGES_OPERATION: ("driver", ("session", "messages", TRIAL_SESSION, "--view-bot", DRIVER_BOT, "--limit", "50")),
            CREATE_PERSISTENT_GROUP_OPERATION: ("driver", ("collaboration", "create", yaml, "--driver", DRIVER_BOT, "--binding", f"planner={DRIVER_BOT}", "--binding", f"reviewer={MEMBER_BOT}", "--context", CUSTOM_COLLABORATION_CONTEXT, "--topic", CUSTOM_COLLABORATION_TOPIC)),
            READ_PERSISTENT_GROUP_OPERATION: ("driver", ("get-group", "--id", PERSIST_GROUP)),
        }
        self.assertEqual(set(rows), set(LEAF_ARGUMENTS))
        for operation, (role, expected_argv) in rows.items():
            with self.subTest(operation=operation):
                self.assertEqual(runner._contract(operation, **self.kwargs_for(operation)), (role, expected_argv))

    def test_command_envelope_carries_origin_bounds_and_removes_the_root(self):
        runner = self.fresh()
        with patch.dict(os.environ, SECRETS), self.launcher(stdout=bytes_json({"valid": True, "summary": {"participants": 2, "nodes": 2}})) as capture:
            result = runner.execute(VALIDATE_YAML_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
        self.assertEqual(result["data"]["summary"], {"participants": 2, "nodes": 2})
        evidence = result["evidence"]
        self.assertEqual((evidence["transport"], evidence["caller_role"], evidence["credential_kind"], evidence["remote_validation"], evidence["grants_release_credit"]), ("PRODUCT_CLI", "operator", "none", "NOT_RUN", False))
        one = capture.calls[0]
        self.assertEqual(one["command"], [str(self.cli_binary.resolve()), "--url", "http://127.0.0.1:21000", "--json", "--log-level", "error", "collaboration", "validate", str(self.state / cc_run.DEFINITION_FILE)])
        self.assertEqual((one["timeout_seconds"], one["max_output_bytes"]), (10, 65536))
        root, environment = Path(one["cwd"]), one["environment"]
        self.assertTrue(str(root).startswith(str(self.launch_base)))
        self.assertEqual((environment["LANG"], environment["DOCKER_HOST"], environment["PATH"].split(":")[0]), ("C", "unix:///kept/docker.sock", "/usr/local/bin"))
        self.assertTrue(all(environment[key].startswith(str(root)) for key in ("HOME", "BOT_DATA_DIR", "TMPDIR")))
        self.assertNotIn("BCS_COOKIE", environment)
        self.assertNotIn("tc_sdb_nenv", environment)
        self.assertEqual(capture.staged_session, {"token": "", "bot_uuid": DRIVER_BOT, "api_base_url": "http://127.0.0.1:21000"})
        self.assertFalse(root.exists())

    def test_private_context_scrubs_and_stages_credentials_by_tier(self):
        rows = (
            (profile_value(), DRIVER_BOT, MEMBER_BOT, "owned_local_bot_token", {"token": DRIVER_TOKEN, "bot_uuid": DRIVER_BOT, "api_base_url": "http://127.0.0.1:21000"}, False),
            (profile_value(preprod=True, auth=trio_auth()), PREPROD_DRIVER, PREPROD_MEMBER, "human_cookie_with_owned_driver_bot_token", {"token": DRIVER_TOKEN, "bot_uuid": PREPROD_DRIVER, "api_base_url": "https://bcs-pre.alipay.com"}, True),
            (profile_value(preprod=True, auth=cookie_auth()), PREPROD_DRIVER, PREPROD_MEMBER, "human_cookie", {"token": "", "bot_uuid": PREPROD_DRIVER, "api_base_url": "https://bcs-pre.alipay.com"}, True),
        )
        for profile, driver, member, kind, staged, cookie_routed in rows:
            with self.subTest(tier=profile["environment"]["tier"], kind=kind):
                runner = self.fresh(profile)
                with patch.dict(os.environ, SECRETS), self.launcher(stdout=[bytes_json({"valid": True, "summary": {"participants": 2, "nodes": 2}}),
                        bytes_json({"id": TRIAL_GROUP, "session_id": TRIAL_SESSION, "driver_bot": driver,
                                    "participants": [driver, member], "created": True})]) as capture:
                    runner.execute(VALIDATE_YAML_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
                    result = runner.execute(TRIAL_CREATE_GROUP_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
                self.assertEqual(result["data"], cli_projection(profile, TRIAL_CREATE_GROUP_OPERATION))
                self.assertEqual(result["evidence"]["credential_kind"], kind)
                self.assertEqual(capture.staged_session, staged)
                validation_environment = capture.calls[0]["environment"]
                if cookie_routed:
                    self.assertEqual((validation_environment["BCS_COOKIE"], validation_environment["tc_sdb_nenv"]), (HUMAN_COOKIE, "production"))
                else:
                    self.assertNotIn("BCS_COOKIE", validation_environment)
                    self.assertNotIn("tc_sdb_nenv", validation_environment)
                environment = capture.calls[1]["environment"]
                if cookie_routed:
                    self.assertEqual((environment["BCS_COOKIE"], environment["tc_sdb_nenv"]), (HUMAN_COOKIE, "production"))
                else:
                    self.assertNotIn("BCS_COOKIE", environment)
                    self.assertNotIn("tc_sdb_nenv", environment)
                self.assertEqual(list(self.launch_base.glob("runner-*")), [])
                self.assertNotIn(DRIVER_TOKEN, json.dumps(result, sort_keys=True))

    def test_validation_leaf_presents_identity_only_on_remote_tiers(self):
        # Live preprod r1/r2: the station ACE gateway identity-gates the
        # app-unauthenticated validation route (anonymous and driver-token
        # requests refused with USER_NOT_LOGIN; the Human Cookie accepted),
        # while the owned Singlebox face keeps its anonymous preflight.
        with patch.dict(os.environ, SECRETS), self.launcher(stdout=bytes_json({"valid": True, "summary": {"participants": 2, "nodes": 2}})) as capture:
            self.fresh().execute(VALIDATE_YAML_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
        local_environment = capture.calls[0]["environment"]
        self.assertNotIn("BCS_COOKIE", local_environment)
        self.assertNotIn("tc_sdb_nenv", local_environment)
        self.assertEqual(capture.staged_session,
                         {"token": "", "bot_uuid": DRIVER_BOT, "api_base_url": "http://127.0.0.1:21000"})
        with patch.dict(os.environ, SECRETS), self.launcher(stdout=bytes_json({"valid": True, "summary": {"participants": 2, "nodes": 2}})) as capture:
            self.fresh(preprod_profile(trio_auth())).execute(VALIDATE_YAML_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
        remote_environment = capture.calls[0]["environment"]
        self.assertEqual((remote_environment["BCS_COOKIE"], remote_environment["tc_sdb_nenv"]), (HUMAN_COOKIE, "production"))
        self.assertEqual(capture.staged_session,
                         {"token": DRIVER_TOKEN, "bot_uuid": PREPROD_DRIVER, "api_base_url": "https://bcs-pre.alipay.com"})

    def test_missing_credential_env_is_rejected(self):
        rows = ((self.fresh(), {}, "credential is absent"), (self.fresh(preprod_profile(trio_auth())), {DRIVER_TOKEN_ENV: DRIVER_TOKEN}, "bundle"), (self.fresh(preprod_profile(cookie_auth())), {DRIVER_TOKEN_ENV: DRIVER_TOKEN}, "Human Cookie is absent"))
        for runner, env, where in rows:
            with self.subTest(where=where):
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaisesRegex(CustomCollaborationCliRunnerError, where):
                        runner.execute(VALIDATE_YAML_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)

    def test_respond_guard_refusal_branches(self):
        def walked(guard_exit, through_guard):
            ops = CUSTOM_COLLABORATION_CLI_OPERATIONS[:8 if through_guard else 7]
            runner = self.fresh()
            with patch.dict(os.environ, SECRETS), patch.object(cc_leaf, "capture", SequenceCapture(local_profile(), guard_exit=guard_exit)):
                for operation in ops:
                    runner.execute(operation, **{key: value for key, value in {"yaml_path": self.state / cc_run.DEFINITION_FILE, "session_id": TRIAL_SESSION, "run_id": TRIAL_RUN, "human_id": "human_" + HUMAN}.items() if key in LEAF_ARGUMENTS[operation][2]})
            return runner
        self.assertEqual(walked(1, True).executed[-1], TRIAL_RESPOND_GUARD_OPERATION)
        pattern = walked(1, False)
        with patch.dict(os.environ, SECRETS), patch.object(cc_leaf, "capture", SequenceCapture(local_profile())):
            guard = pattern.execute(TRIAL_RESPOND_GUARD_OPERATION, run_id=TRIAL_RUN)
        self.assertEqual((guard["data"], guard["evidence"]["exit_status"]), ({"guard_refused": True, "node": "plan", "exit_code": 1, "not_pending": True}, 1))
        zero = walked(0, False)
        with patch.dict(os.environ, SECRETS), patch.object(cc_leaf, "capture", SequenceCapture(local_profile(), guard_exit=0)):
            with self.assertRaisesRegex(CustomCollaborationCliRunnerError, "did not refuse"):
                zero.execute(TRIAL_RESPOND_GUARD_OPERATION, run_id=TRIAL_RUN)

    def test_nonzero_exit_leak_order_and_one_shot_guards(self):
        with patch.dict(os.environ, SECRETS), self.launcher(returncode=2, stdout=b"boom detail"):
            runner = self.fresh()
            with self.assertRaisesRegex(CustomCollaborationCliRunnerError, "nonzero"):
                runner.execute(VALIDATE_YAML_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
        leak = bytes_json({"id": TRIAL_GROUP, "session_id": TRIAL_SESSION,
                           "note": DRIVER_TOKEN})
        runner = self.fresh()
        with patch.dict(os.environ, SECRETS), self.launcher(stdout=bytes_json({"valid": True, "summary": {"participants": 2, "nodes": 2}})):
            runner.execute(VALIDATE_YAML_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
        with patch.dict(os.environ, SECRETS), self.launcher(stdout=leak):
            with self.assertRaisesRegex(CustomCollaborationCliRunnerError, "emitted its credential"):
                runner.execute(TRIAL_CREATE_GROUP_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
        once = self.fresh()
        with patch.dict(os.environ, SECRETS), self.launcher(stdout=[bytes_json({"valid": True, "summary": {"participants": 2, "nodes": 2}}),
                bytes_json({"id": TRIAL_GROUP, "session_id": TRIAL_SESSION, "driver_bot": DRIVER_BOT,
                            "participants": [DRIVER_BOT, MEMBER_BOT], "created": True})]):
            once.execute(VALIDATE_YAML_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
            once.execute(TRIAL_CREATE_GROUP_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
            with self.assertRaisesRegex(CustomCollaborationCliRunnerError, "already executed"):
                once.execute(TRIAL_CREATE_GROUP_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
        with self.assertRaisesRegex(CustomCollaborationCliRunnerError, "cannot be reordered or skipped"):
            self.fresh().execute(TRIAL_QUERY_GRAPH_OPERATION, run_id=TRIAL_RUN)

    def test_definition_drift_missing_symlink_or_oversized_is_rejected(self):
        definition, runner = self.state / cc_run.DEFINITION_FILE, self.fresh()
        with self.launcher():
            definition.unlink()
            with self.assertRaisesRegex(CustomCollaborationCliRunnerError, "missing"):
                runner.execute(VALIDATE_YAML_OPERATION, yaml_path=definition)
            definition.write_text(DEFINITION_YAML.replace("plan", "plant"), encoding="utf-8")
            with self.assertRaisesRegex(CustomCollaborationCliRunnerError, "differs"):
                runner.execute(VALIDATE_YAML_OPERATION, yaml_path=definition)
            definition.write_bytes(b"x" * 1_048_577)
            with self.assertRaisesRegex(CustomCollaborationCliRunnerError, "differs"):
                runner.execute(VALIDATE_YAML_OPERATION, yaml_path=definition)
            definition.unlink()
            definition.symlink_to(self.root / "elsewhere.yaml")
            with self.assertRaisesRegex(CustomCollaborationCliRunnerError, "missing"):
                runner.execute(VALIDATE_YAML_OPERATION, yaml_path=definition)

    def test_parse_projections_and_drift(self):
        runner = self.fresh()
        good = (
            (VALIDATE_YAML_OPERATION, bytes_json({"valid": True, "summary": {"participants": 2, "nodes": 2}}), {}),
            (CREATE_PERSISTENT_GROUP_OPERATION, bytes_json({"id": PERSIST_GROUP, "session_id": PERSIST_SESSION, "driver_bot": DRIVER_BOT, "participants": [DRIVER_BOT, MEMBER_BOT], "created": True}), {}),
            (READ_PERSISTENT_GROUP_OPERATION, bytes_json({"id": PERSIST_GROUP, "driver_bot": DRIVER_BOT, "group_strategy": "state_machine", "participants": [{"bot_uuid": DRIVER_BOT}, {"bot_uuid": MEMBER_BOT}]}), {"group_id": PERSIST_GROUP}),
            (TRIAL_CREATE_GROUP_OPERATION, bytes_json({"id": TRIAL_GROUP, "session_id": TRIAL_SESSION, "driver_bot": DRIVER_BOT, "participants": [DRIVER_BOT, MEMBER_BOT], "created": True}), {}),
            (TRIAL_ADD_MEMBER_OPERATION, bytes_json({"session_id": TRIAL_SESSION, "participants": [DRIVER_BOT, MEMBER_BOT, {"bot_uuid": "human_" + HUMAN, "role": "consultant"}]}), {"session_id": TRIAL_SESSION, "human_id": "human_" + HUMAN}),
            (TRIAL_PERMISSION_OPERATION, bytes_json({"allowed": True, "session_id": TRIAL_SESSION, "group_id": TRIAL_GROUP, "policy_version": "session_state_machine_v1", "group_strategy": "chat", "caller_bot_id": DRIVER_BOT}), {"session_id": TRIAL_SESSION}),
            (TRIAL_START_RUN_OPERATION, bytes_json({"run": {"run_id": TRIAL_RUN, "session_id": TRIAL_SESSION, "group_id": TRIAL_GROUP, "status": "running"}}), {"session_id": TRIAL_SESSION}),
            (TRIAL_QUERY_GRAPH_OPERATION, bytes_json({"run": {"run_id": TRIAL_RUN, "session_id": TRIAL_SESSION, "group_id": TRIAL_GROUP}, "nodes": [{"node_id": "plan", "status": "completed"}]}), {"run_id": TRIAL_RUN}),
            (TRIAL_QUERY_PENDING_OPERATION, b"[]", {}),
            (TRIAL_MESSAGES_OPERATION, bytes_json([{"view": "bcsPanel.StateMachineRunView", "run_id": TRIAL_RUN, "session_id": TRIAL_SESSION}]), {"session_id": TRIAL_SESSION, "run_id": TRIAL_RUN}),
        )
        drift = (
            (VALIDATE_YAML_OPERATION, bytes_json({"valid": True, "summary": {"participants": 2, "nodes": 3}}), {}),
            (CREATE_PERSISTENT_GROUP_OPERATION, bytes_json({"id": PERSIST_GROUP, "session_id": PERSIST_SESSION, "driver_bot": MEMBER_BOT, "participants": [DRIVER_BOT], "created": True}), {}),
            (READ_PERSISTENT_GROUP_OPERATION, bytes_json({"id": "other", "driver_bot": DRIVER_BOT, "group_strategy": "chat", "participants": [DRIVER_BOT]}), {"group_id": PERSIST_GROUP}),
            (TRIAL_CREATE_GROUP_OPERATION, bytes_json({"id": TRIAL_GROUP, "session_id": TRIAL_SESSION, "driver_bot": DRIVER_BOT, "participants": [MEMBER_BOT], "created": True}), {}),
            (TRIAL_ADD_MEMBER_OPERATION, bytes_json({"session_id": TRIAL_SESSION, "participants": [DRIVER_BOT, MEMBER_BOT]}), {"session_id": TRIAL_SESSION, "human_id": "human_" + HUMAN}),
            (TRIAL_PERMISSION_OPERATION, bytes_json({"allowed": False, "session_id": TRIAL_SESSION, "group_id": TRIAL_GROUP, "policy_version": "session_state_machine_v1", "group_strategy": "chat", "caller_bot_id": DRIVER_BOT}), {"session_id": TRIAL_SESSION}),
            (TRIAL_START_RUN_OPERATION, bytes_json({"run": {"run_id": TRIAL_RUN, "session_id": "other-session", "group_id": TRIAL_GROUP, "status": "running"}}), {"session_id": TRIAL_SESSION}),
            (TRIAL_QUERY_GRAPH_OPERATION, bytes_json({"run": {"run_id": "other"}, "nodes": []}), {"run_id": TRIAL_RUN}),
            (TRIAL_QUERY_PENDING_OPERATION, bytes_json([{"human": True}]), {}),
            (TRIAL_MESSAGES_OPERATION, bytes_json([{"view": "other", "run_id": "absent"}]), {"session_id": TRIAL_SESSION, "run_id": TRIAL_RUN}),
        )
        for rows, accepted in ((good, True), (drift, False)):
            for operation, payload, kwargs in rows:
                with self.subTest(operation=operation, accepted=accepted):
                    if accepted:
                        self.assertIsNotNone(runner._parse(operation, payload, **kwargs))
                    else:
                        with self.assertRaises(CustomCollaborationCliRunnerError):
                            runner._parse(operation, payload, **kwargs)

    def test_capture_cleanup_and_construction_failures_project_closed(self):
        runner = self.fresh()
        with patch.dict(os.environ, SECRETS), self.launcher(side_effect=cc_leaf.ProcessCaptureError("process died")):
            with self.assertRaisesRegex(CustomCollaborationCliRunnerError, "process failed"):
                runner.execute(VALIDATE_YAML_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
        failure = cc_leaf.ProcessCaptureError("shutdown unconfirmed")
        failure.child_shutdown_status = "UNCONFIRMED"
        with patch.dict(os.environ, SECRETS), self.launcher(side_effect=failure):
            try:
                runner.execute(VALIDATE_YAML_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
            except CustomCollaborationCliRunnerError as error:
                self.assertEqual(error.child_shutdown_status, "UNCONFIRMED")
            else:
                self.fail("unconfirmed shutdown must surface publicly")
        with patch.dict(os.environ, SECRETS), self.launcher(side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                runner.execute(VALIDATE_YAML_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
        runner2 = self.fresh()
        with patch.dict(os.environ, SECRETS), self.launcher(stdout=bytes_json({"valid": True, "summary": {"participants": 2, "nodes": 2}})):
            runner2.execute(VALIDATE_YAML_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
        with patch.dict(os.environ, SECRETS), self.launcher(stdout=bytes_json({"id": TRIAL_GROUP, "session_id": TRIAL_SESSION, "driver_bot": DRIVER_BOT, "participants": [DRIVER_BOT, MEMBER_BOT], "created": True})), patch.object(cc_leaf.shutil, "rmtree", side_effect=OSError("kept")):
            with self.assertRaisesRegex(CustomCollaborationCliRunnerError, "cleanup is unconfirmed") as caught:
                runner2.execute(TRIAL_CREATE_GROUP_OPERATION, yaml_path=self.state / cc_run.DEFINITION_FILE)
        self.assertEqual(caught.exception.credential_cleanup_status, "UNCONFIRMED")
        staged = json.loads(next(self.launch_base.glob("runner-*/bot/.bcs/session.json")).read_text(encoding="utf-8"))
        self.assertEqual(staged, {"token": "", "bot_uuid": "", "api_base_url": ""})
        with self.assertRaisesRegex(CustomCollaborationCliRunnerError, "absolute current Run directory"):
            CustomCollaborationCliRunner(local_profile(), binary=self.cli_binary, binary_sha256=self.cli_sha256, state_dir=Path("relative/state"))
        bad = self.root / "bad-bcs-cli"
        bad.write_bytes(b"#!/bin/sh\nexit 0\n")
        bad.chmod(0o700)
        with self.assertRaisesRegex(CustomCollaborationCliRunnerError, "binary is not approved"):
            CustomCollaborationCliRunner(local_profile(), binary=bad, binary_sha256="0" * 64, state_dir=self.state)

    def test_full_frozen_leaf_sequence_drives_the_dual_case_to_pass(self):
        for profile in (local_profile(), preprod_profile(trio_auth()), preprod_profile(cookie_auth())):
            with self.subTest(tier=profile["environment"]["tier"]):
                FakeWriteClient.instances.clear()
                FakeWriteClient.failed = False
                runner = CustomCollaborationCliRunner(profile, binary=self.cli_binary, binary_sha256=self.cli_sha256, state_dir=self.state)
                with patch.dict(os.environ, SECRETS), patch.object(cc_leaf, "capture", SequenceCapture(profile)), patch.object(cc_run, "WriteClient", FakeWriteClient):
                    adapter = DualCustomCollaborationAdapter(profile, runner)
                    execution = execute_custom_collaboration_case(adapter)
                self.assertEqual((execution.status, execution.completed_count), ("PASS", 37))
                self.assertEqual(runner.executed, list(CUSTOM_COLLABORATION_CLI_OPERATIONS))
                self.assertEqual([operation for operation, _ in FakeWriteClient.instances[0].calls], [SESSION_READ_OPERATION, GROUP_DELETE_OPERATION, GROUP_VERIFY_DELETED_OPERATION, GROUP_DELETE_OPERATION, GROUP_VERIFY_DELETED_OPERATION])
                self.assertEqual([operation for operation, _ in FakeWriteClient.instances[-1].calls], [RUN_CANCEL_OPERATION])
                records = [record for step in execution.steps for record in step.evidence["requests"]]
                self.assertEqual(len(records), len(CUSTOM_COLLABORATION_CASE_METHODS) + len(DENSITY_SPECS))
                for (operation, _method), record in zip(CUSTOM_COLLABORATION_CASE_METHODS, records):
                    self.assertEqual(record["transport"], expected_step_transport(operation))
                    self.assertEqual(record["credential_kind"], custom_collaboration_step_credential_kind(profile, operation))


class CustomCollaborationRunEntryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.cli_binary, self.cli_sha256 = make_cli(self.root)
        FakeWriteClient.instances.clear()
        FakeWriteClient.failed = False

    def write_profile(self, profile=None):
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(profile or local_profile()), encoding="utf-8")
        return profile_path

    def entry(self, *, profile=None, runner=None, real_executor=False):
        stack = ExitStack()
        stack.enter_context(patch.object(cc_run, "CustomCollaborationCliRunner", runner or FakeChainRunner))
        stack.enter_context(patch.object(cc_run, "WriteClient", FakeWriteClient))
        if not real_executor:
            stack.enter_context(patch.object(cc_run, "execute_custom_collaboration_case", lambda adapter: fake_execution()))
        with stack:
            state_dir = self.root / f"state-{len(FakeWriteClient.instances)}"
            report = dual_custom_collaboration_case_run(profile_path=self.write_profile(profile), cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha256, state_dir=state_dir, run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID)
        return report, state_dir

    def test_full_dual_pass_reports_every_gate_field_and_receipt(self):
        report, state_dir = self.entry()
        profile = local_profile()
        self.assertEqual((report["status"], report["case_id"], report["parent_id"], report["run_id"], report["credential_kind"], report["remote_validation"], report["grants_release_credit"], report["resource_disposition"], report["cli_binary_sha256"], report["profile_digest"]), ("PASS", DUAL_CUSTOM_COLLAB_CASE_ID, PARENT, RUN_ID, "owned_local_bot_token", "NOT_RUN", False, CUSTOM_COLLABORATION_CLI_EFFECT, self.cli_sha256, fingerprint(profile)))
        self.assertTrue(report["executor_digest"])
        self.assertEqual(report["environment"], {"tier": "local", "id": "custom-collaboration-cli-local", "entrypoint_kind": "singlebox"})
        self.assertEqual(report["execution"], {"status": "PASS", "completed_count": 1, "total_count": 1, "error_type": None, "error_detail": None})
        self.assertEqual((state_dir / cc_run.DEFINITION_FILE).read_text(encoding="utf-8"), DEFINITION_YAML)
        self.assertEqual(read_receipt(state_dir / "custom-collaboration-profile.json"), profile)
        self.assertEqual(read_receipt(state_dir / "custom-collaboration-binding.json"), {"schema_version": cc_run.SCHEMA_VERSION, "contract": "dual-custom-collaboration-case-run-v2", "case_id": DUAL_CUSTOM_COLLAB_CASE_ID, "parent_id": PARENT, "run_id": RUN_ID, "profile_digest": fingerprint(profile), "executor_digest": report["executor_digest"], "cli_binary_sha256": self.cli_sha256, "state_dir": str(state_dir.resolve())})
        self.assertEqual(report["case"]["status"], "PASS")
        self.assertEqual((state_dir / "custom-collaboration-run.json").read_text(encoding="utf-8"), json.dumps(report, sort_keys=True, indent=2) + "\n")
        self.assertEqual(state_dir.stat().st_mode & 0o777, 0o700)
        for path in state_dir.iterdir():
            content = path.read_text(encoding="utf-8", errors="replace")
            for secret in (DRIVER_TOKEN, MEMBER_TOKEN, HUMAN_COOKIE):
                self.assertNotIn(secret, content)

    def test_preprod_report_records_the_credential_family_without_secret(self):
        for auth, kind in ((trio_auth(), "human_cookie_with_owned_driver_bot_token"), (cookie_auth(), "human_cookie")):
            with self.subTest(auth=auth["kind"]):
                report, _ = self.entry(profile=preprod_profile(auth))
                self.assertEqual((report["status"], report["credential_kind"], report["environment"]), ("PASS", kind, {"tier": "preprod", "id": "custom-collaboration-cli-preprod", "entrypoint_kind": "gateway"}))
                serialized = json.dumps(report, sort_keys=True)
                self.assertNotIn(HUMAN_COOKIE, serialized)
                self.assertNotIn(DRIVER_TOKEN, serialized)

    def test_blocked_receipt_projects_public_failure_class_without_secret(self):
        class NonzeroChainRunner(FakeChainRunner):
            def __init__(self, profile, **kwargs):
                super().__init__(profile, **kwargs)
                self.fail_at = VALIDATE_YAML_OPERATION

            def execute(self, operation, **kwargs):
                if operation == self.fail_at:
                    raise CustomCollaborationCliRunnerError(
                        "custom collaboration product CLI returned nonzero: scrubbed detail")
                return super().execute(operation, **kwargs)

        report, _ = self.entry(runner=NonzeroChainRunner, real_executor=True)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual((report["failed_method"], report["failure_class"]),
                         ("validate_yaml", "PRODUCT_CLI_NONZERO_EXIT"))
        for step in report["case"]["steps"]:
            self.assertEqual(step["error_gap"], "PRODUCT_CLI_NONZERO_EXIT")
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("scrubbed detail", serialized)
        self.assertNotIn(DRIVER_TOKEN, serialized)

    def test_blocked_run_writes_sanitized_receipts_and_blocked_report(self):
        class FailingChainRunner(FakeChainRunner):
            def __init__(self, profile, **kwargs):
                super().__init__(profile, **kwargs)
                self.fail_at = TRIAL_CREATE_GROUP_OPERATION

        report, state_dir = self.entry(runner=FailingChainRunner, real_executor=True)
        self.assertEqual(report["status"], "BLOCKED")
        execution = report["execution"]
        self.assertEqual((execution["status"], execution["completed_count"], execution["total_count"], execution["error_type"]), ("BLOCKED", 0, 37, "RunStateError"))
        self.assertIn("custom_collaboration_cli.trial_create_group", execution["error_detail"])
        self.assertIn("CASE_CONTRACT_VIOLATION", execution["error_detail"])
        self.assertEqual(report["case"]["status"], "BLOCKED")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertNotIn(DRIVER_TOKEN, json.dumps(report, sort_keys=True))
        self.assertTrue((state_dir / "custom-collaboration-run.json").is_file())
        for name in ("custom-collaboration-profile.json", "custom-collaboration-binding.json", cc_run.DEFINITION_FILE):
            self.assertTrue((state_dir / name).is_file())

    def test_runner_and_transport_failures_wrap_into_config_error(self):
        profile_path = self.write_profile()
        base = dict(cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha256, run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID)
        with patch.object(cc_run, "CustomCollaborationCliRunner", ExplodingRunner):
            with self.assertRaisesRegex(ConfigError, "case execution is unavailable"):
                dual_custom_collaboration_case_run(profile_path=profile_path, state_dir=self.root / "s1", **base)
        self.assertFalse((self.root / "s1" / "custom-collaboration-run.json").exists())
        for name in ("custom-collaboration-profile.json", "custom-collaboration-binding.json", cc_run.DEFINITION_FILE):
            self.assertTrue((self.root / "s1" / name).is_file())
        with patch.object(cc_run, "CustomCollaborationCliRunner", FakeChainRunner), patch.object(cc_run, "WriteClient", ExplodingWriteClient):
            with self.assertRaisesRegex(ConfigError, "case execution is unavailable"):
                dual_custom_collaboration_case_run(profile_path=profile_path, state_dir=self.root / "s2", **base)
        self.assertFalse((self.root / "s2" / "custom-collaboration-run.json").exists())

    def test_illegal_profiles_and_placeholders_fail_closed_before_state_creation(self):
        state_dir = self.root / "state"
        for index, (raw, where) in enumerate((("{not json", "unreadable JSON"), ("[1, 2, 3]", "must be an object"), ('{"schema_version": 1, "schema_version": 1}', "duplicate JSON key"))):
            with self.subTest(where=where):
                path = self.root / f"bad-{index}.json"
                path.write_text(raw, encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, where):
                    load_custom_collaboration_case_profile(path)
        with self.assertRaisesRegex(ConfigError, "regular file"):
            load_custom_collaboration_case_profile(self.root / "missing.json")
        placeholder = profile_value(mutate=lambda value: value["policy"].update({"fixture_pool": "REPLACE_WITH_fixture_pool"}))
        with patch.object(cc_run, "CustomCollaborationCliRunner", FakeChainRunner), patch.object(cc_run, "WriteClient", FakeWriteClient):
            with self.assertRaisesRegex(ConfigError, "REPLACE_WITH_"):
                dual_custom_collaboration_case_run(profile_path=self.write_profile(placeholder), cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha256, state_dir=state_dir, run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID)
        self.assertFalse(state_dir.exists())

    def test_permissions_and_fresh_state_are_enforced(self):
        state_dir, profile_path = self.root / "state", self.write_profile()
        base = dict(cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha256, run_id=RUN_ID)
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_custom_collaboration_case_run(profile_path=profile_path, state_dir=state_dir, allow_write=True, confirm_run_id="other", **base)
        self.assertFalse(state_dir.exists())
        with self.assertRaisesRegex(ConfigError, "explicit write permission"):
            dual_custom_collaboration_case_run(profile_path=profile_path, state_dir=state_dir, allow_write=False, confirm_run_id=RUN_ID, **base)
        state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_custom_collaboration_case_run(profile_path=profile_path, state_dir=state_dir, allow_write=True, confirm_run_id=RUN_ID, **base)
        linked = self.root / "linked"
        linked.symlink_to(self.root / "elsewhere", target_is_directory=True)
        for target in (linked, Path("custom-collab-relative-state"), self.root / "fresh-but-parent-missing" / "deep"):
            with self.subTest(target=str(target)):
                with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
                    dual_custom_collaboration_case_run(profile_path=profile_path, state_dir=target, allow_write=True, confirm_run_id=RUN_ID, **base)

    def test_cli_dispatch_uses_the_dual_custom_collaboration_runner(self):
        if not hasattr(bcs_cli, "dual_custom_collaboration_case_run"):
            self.skipTest("custom-collaboration CLI dispatch is wired by the lead")
        state_dir, profile_path = self.root / "dispatch", self.write_profile()
        arguments = [CASE_COMMAND, "--profile", str(profile_path), "--cli-binary", str(self.cli_binary), "--cli-binary-sha256", self.cli_sha256, "--state-dir", str(state_dir), "--run-id", RUN_ID, "--confirm-run-id", RUN_ID, "--allow-write"]
        with patch("bcs_e2e.cli.dual_custom_collaboration_case_run", return_value={"status": "PASS"}) as execute, redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(profile_path=profile_path, cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha256, state_dir=state_dir, run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


class CustomCollaborationCaseGuardTests(unittest.TestCase):
    def test_case_guard_accepts_the_custom_collaboration_modules(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = (ROOT / "bcs_e2e/custom_collaboration_cli_case_run.py",
                 ROOT / "bcs_e2e/custom_collaboration_cli_leaf.py")
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_quote_path_segment_matches_the_transport_encoder(self):
        for value in ("run-custom-e2e-0001", "20261010_gekng5hk:39464",
                      "20261026_xq102n2b:39464", "a.b-c_d~e", "grp@x:y"):
            with self.subTest(value=value):
                self.assertEqual(cc_run._quote_path_segment(value), quote(value, safe=""))
        for value in ("run id with space", "run%2Fid", ""):
            with self.subTest(value=value):
                if value:
                    with self.assertRaises(RunStateError):
                        cc_run._quote_path_segment(value)
                else:
                    self.assertEqual(cc_run._quote_path_segment(value), quote(value, safe=""))


if __name__ == "__main__":
    unittest.main()
