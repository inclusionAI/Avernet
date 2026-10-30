"""Wave-D2 density regression: field-level obligations for the custom-collaboration parent.

Offline contract tests only.  The three frozen parent obligations ride first;
the 34 source-anchored density atoms (``CUSTOM-COLLABORATION.<atom>``) align
the obligation denominator with the upstream parsed assertion count (37) for
``story_cli_operator_creates_custom_collaboration`` (coverage-map row #14).

Nothing here touches the network, the preprod gateway, a real Singlebox
process or a credential value: the dual envelope is driven by an in-memory
product-CLI runner and an in-memory cleanup WriteClient, profiles reference
environment variable NAMES only, and every fail-close class is asserted
against the live r5/r6 station behaviour (``PRODUCT_CLI_NONZERO_EXIT`` on the
trial create leaf, ``CASE_CONTRACT_VIOLATION`` on projection drift) without
ever fabricating PASS.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bcs_e2e.custom_collaboration_cli_case_run import (
    DUAL_CUSTOM_COLLAB_CASE_ID, PARENT, REQUIREMENTS,
    DualCustomCollaborationAdapter, custom_collaboration_cli_adaptability,
    custom_collaboration_cli_case_plan, execute_custom_collaboration_case,
)
from bcs_e2e.custom_collaboration_cli_leaf import (
    CUSTOM_COLLABORATION_CASE_METHODS, CUSTOM_COLLABORATION_CLI_OPERATIONS,
    CUSTOM_COLLABORATION_CLI_PROFILE_CONTRACT, CUSTOM_COLLABORATION_CLI_RELATIONSHIPS,
    CUSTOM_COLLABORATION_REQUIREMENT_OPERATIONS,
    CREATE_PERSISTENT_GROUP_OPERATION, DECLARED_CLI_EXIT_ROUTES, GROUP_DELETE_OPERATION,
    GROUP_VERIFY_DELETED_OPERATION, LEAF_ARGUMENTS, READ_PERSISTENT_GROUP_OPERATION,
    RUN_CANCEL_OPERATION, SCENARIO_CLEANUP_OPERATIONS, SESSION_READ_OPERATION,
    TRIAL_CREATE_GROUP_OPERATION, TRIAL_MESSAGES_OPERATION, VALIDATE_YAML_OPERATION,
    CustomCollaborationCliRunnerError, custom_collaboration_step_credential_kind,
    expected_step_transport, validate_custom_collaboration_cli_profile,
)
from bcs_e2e.custom_collaboration.density import (
    ATOMS, PREFIX, definition_evidence, panel_evidence, parent_requirements,
    project, requirements as density_requirements, verify,
)
from bcs_e2e.local_cli import LocalCliError
from bcs_e2e.local_custom_collaboration_case import (
    CASE_ID, DENSITY_REQUIREMENTS, DENSITY_SPECS, REQUIREMENTS as LOCAL_REQUIREMENTS,
    custom_collaboration_case_adaptability, custom_collaboration_case_plan,
    custom_collaboration_source_binding, density_assertions, project_density,
    run_custom_collaboration_case, validate_density_contract,
)
from bcs_e2e.local_custom_collaboration_run import DEFINITION_YAML
from bcs_e2e.local_operator_cli_contract import (
    CUSTOM_COLLABORATION_COMMANDS, CUSTOM_COLLABORATION_REQUIREMENTS,
    custom_collaboration_source_binding as operator_source_binding,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import RunStateError
from bcs_e2e.write_transport import WriteTransportError

HUMAN, DRIVER_BOT, MEMBER_BOT = "39464", "driver-custom-collab", "member-custom-collab"
PREPROD_DRIVER = "20261026_xq102n2b:39464"
DRIVER_TOKEN_ENV, MEMBER_TOKEN_ENV, COOKIE_ENV = (
    "BCS_E2E_DRIVER_BOT_TOKEN", "BCS_E2E_MEMBER_BOT_TOKEN", "BCS_E2E_HUMAN_COOKIE")
TRIAL_GROUP, TRIAL_SESSION = "grp-custom-trial-0001", "grp-custom-trial-0001:00000001"
PERSIST_GROUP, PERSIST_SESSION = "grp-custom-persistent-0002", "grp-custom-persistent-0002:00000001"
TRIAL_RUN = "run-custom-e2e-0001"
CLI_STORIES = Path(__file__).resolve().parents[2] / "e2e-test" / "cli-stories.sh"


def route_bindings():
    return {operation: {"method": method, "path": path, "api_face": "legacy",
                        "auth": "case_owned_driver", "response": "legacy-json",
                        "traceparent": "disabled"}
            for operation, (method, path) in DECLARED_CLI_EXIT_ROUTES.items()}


def profile_value(*, preprod=False, mutate=None):
    driver, member = (("20261026_xq102n2b:39464", "20261010_gekng5hk:39464") if preprod
                      else (DRIVER_BOT, MEMBER_BOT))
    value = {
        "schema_version": 1, "contract": CUSTOM_COLLABORATION_CLI_PROFILE_CONTRACT,
        "environment": {"tier": "preprod" if preprod else "local",
                        "id": "custom-collaboration-cli-preprod" if preprod
                        else "custom-collaboration-cli-local",
                        "expected_bcs_env": "pre" if preprod else "local"},
        "entrypoint": {"kind": "gateway" if preprod else "singlebox",
                       "origin": "https://bcs-pre.alipay.com" if preprod
                       else "http://127.0.0.1:21000",
                       "api_face": "legacy", "manifest_face": "legacy"},
        "human": {"user_id": HUMAN},
        "actors": {"driver": {"bot_id": driver, "owner_id": HUMAN, "dedicated": True,
                              "visibility": "public"},
                   "member": {"bot_id": member, "owner_id": HUMAN, "dedicated": True,
                              "visibility": "public"}},
        "auth": ({"kind": "human-cookie", "cookie_env": COOKIE_ENV} if preprod else
                 {"kind": "owned-local-bot-tokens", "driver_token_env": DRIVER_TOKEN_ENV,
                  "member_token_env": MEMBER_TOKEN_ENV}),
        "relationships": dict(CUSTOM_COLLABORATION_CLI_RELATIONSHIPS),
        "policy": {"fixture_pool": "custom-collaboration-cli-pool",
                   "exclusive_use_confirmed": True, "timeout_seconds": 10,
                   "max_response_bytes": 65536, "failure_retention_hours": 24},
        "cli": {"transport": "product-cli", "command_family": "collaboration",
                "api_face": "legacy"},
        "route_bindings": route_bindings()}
    if preprod:
        value["browser_origin"] = "https://teamclaw-pre.alipay.com"
    if mutate is not None:
        mutate(value)
    return value


class FakeChainRunner:
    """In-memory product-CLI leaf runner: no process, no network, no secrets."""

    def __init__(self, profile, *, fail_at=None, failure=None):
        self.profile, self.state_dir = profile, Path("/owned-state")
        self.definition_path = Path("/owned-state/custom-collaboration-definition.yaml")
        self.calls, self.fail_at, self.failure = [], fail_at, failure

    def execute(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation == self.fail_at:
            raise (self.failure or RuntimeError("simulated product CLI failure"))
        return {"data": self.projection(operation, kwargs), "evidence": self.evidence(operation)}

    def evidence(self, operation):
        return {"transport": "PRODUCT_CLI", "operation": operation,
                "caller_role": LEAF_ARGUMENTS[operation][0], "command_arguments": [],
                "http_method_declared": "CLI", "route_declared": "product-cli",
                "credential_kind": custom_collaboration_step_credential_kind(self.profile, operation),
                "exit_status": 0, "elapsed_ms": 1.0, "remote_validation": "NOT_RUN",
                "grants_release_credit": False}

    def projection(self, operation, kwargs):
        driver = self.profile["actors"]["driver"]["bot_id"]
        member = self.profile["actors"]["member"]["bot_id"]
        human, session = "human_" + HUMAN, kwargs.get("session_id", TRIAL_SESSION)
        group = kwargs.get("group_id", TRIAL_GROUP)
        if operation == VALIDATE_YAML_OPERATION:
            return {"valid": True, "summary": {"participants": 2, "nodes": 2}}
        if operation == CREATE_PERSISTENT_GROUP_OPERATION:
            return {"id": PERSIST_GROUP, "session_id": PERSIST_SESSION, "driver_bot": driver,
                    "participants": [driver, member], "created": True}
        if operation == READ_PERSISTENT_GROUP_OPERATION:
            return {"id": PERSIST_GROUP, "driver_bot": driver,
                    "group_strategy": "state_machine",
                    "participants": [{"bot_uuid": driver}, {"bot_uuid": member}]}
        if operation == TRIAL_CREATE_GROUP_OPERATION:
            return {"id": TRIAL_GROUP, "session_id": TRIAL_SESSION, "driver_bot": driver,
                    "participants": [driver, member], "created": True}
        if operation.endswith("trial_add_member"):
            return {"session_id": session,
                    "participants": [driver, member, {"bot_uuid": human, "role": "consultant"}]}
        if operation.endswith("trial_permission"):
            return {"allowed": True, "session_id": session, "group_id": group,
                    "policy_version": "session_state_machine_v1", "group_strategy": "chat",
                    "caller_bot_id": driver}
        if operation.endswith("trial_start_run"):
            return {"run": {"run_id": TRIAL_RUN, "session_id": session,
                            "group_id": group, "status": "running"}}
        if operation.endswith("trial_query_graph"):
            return {"run": {"run_id": kwargs["run_id"], "session_id": session,
                            "group_id": group},
                    "nodes": [{"node_id": "plan", "status": "completed"},
                              {"node_id": "review", "status": "running"}]}
        if operation.endswith("trial_query_pending"):
            return []
        if operation.endswith("trial_respond_guard"):
            return {"guard_refused": True, "node": "plan", "exit_code": 1, "not_pending": True}
        if operation == TRIAL_MESSAGES_OPERATION:
            return [{"view": "bcsPanel.StateMachineRunView", "run_id": kwargs["run_id"],
                     "session_id": session}]
        raise AssertionError(f"unexpected operation {operation}")


class FakeWriteClient:
    instances = []
    failed = False

    def __init__(self, profile, *, local_auth=None):
        self.profile, self.calls = profile, []
        FakeWriteClient.instances.append(self)

    def call(self, operation, *, group_id="", session_id="", body=None,
             expected=(200,), parse_error=False):
        self.calls.append((operation, dict(group_id=group_id, session_id=session_id, body=body)))
        if FakeWriteClient.failed:
            raise WriteTransportError("simulated cleanup transport failure")
        if operation == RUN_CANCEL_OPERATION:
            return ({"run": {"run_id": TRIAL_RUN, "status": "aborted"}},
                    {"operation": operation, "http_status": 200})
        if operation == GROUP_DELETE_OPERATION:
            return ({"deleted": True, "id": group_id},
                    {"operation": operation, "http_status": 200})
        if operation == GROUP_VERIFY_DELETED_OPERATION:
            return ({"status": 404, "code": "group_not_found"},
                    {"operation": operation, "http_status": 404})
        return ({"session_id": session_id, "group_id": group_id, "status": "running"},
                {"operation": operation, "http_status": 200})


def dual_chain(*, preprod=False, fail_at=None, failure=None):
    profile = validate_custom_collaboration_cli_profile(profile_value(preprod=preprod))
    runner = FakeChainRunner(profile, fail_at=fail_at, failure=failure)
    with patch("bcs_e2e.custom_collaboration_cli_case_run.WriteClient", FakeWriteClient):
        adapter = DualCustomCollaborationAdapter(profile, runner)
        execution = execute_custom_collaboration_case(adapter)
    return adapter, runner, execution


def full_events():
    return {
        "validate": {"valid": True, "summary": {"participants": 2, "nodes": 2}},
        "definition": definition_evidence(DEFINITION_YAML),
        "trial": {"session_id": TRIAL_SESSION},
        "member": {"session_id": TRIAL_SESSION,
                   "participants": [{"bot_uuid": "human_" + HUMAN, "role": "consultant"}]},
        "permission": {"allowed": True, "session_id": TRIAL_SESSION, "group_id": TRIAL_GROUP},
        "run": {"run_id": TRIAL_RUN, "session_id": TRIAL_SESSION, "group_id": TRIAL_GROUP},
        "graph": {"run_id": TRIAL_RUN},
        "pending": [],
        "guard": {"exit_code": 1, "not_pending": True},
        "panel": panel_evidence([{"view": "bcsPanel.StateMachineRunView", "run_id": TRIAL_RUN}], TRIAL_RUN),
        "cancel": {"run": {"run_id": TRIAL_RUN, "status": "aborted"}},
        "session": {"session_id": TRIAL_SESSION, "group_id": TRIAL_GROUP, "status": "running"},
        "trial_deleted": {"id": TRIAL_GROUP, "deleted": True},
        "persistent": {"id": PERSIST_GROUP, "participants": [DRIVER_BOT, MEMBER_BOT]},
        "persistent_read": {"id": PERSIST_GROUP, "group_strategy": "state_machine"},
        "persistent_deleted": {"id": PERSIST_GROUP, "deleted": True},
    }


def full_bindings():
    return {"driver": DRIVER_BOT, "reviewer": MEMBER_BOT, "human": "human_" + HUMAN,
            "trial_group": TRIAL_GROUP, "trial_session": TRIAL_SESSION,
            "run_id": TRIAL_RUN, "persistent_group": PERSIST_GROUP}


def full_base():
    return {row["id"]: "PASS" for row in parent_requirements()}


class DensityObligationBuilderTests(unittest.TestCase):
    def test_builder_counts_align_the_denominator_to_parsed_37(self):
        binding = operator_source_binding()
        self.assertEqual(len(binding["requirements"]), 3)
        self.assertEqual(len(binding["density_requirements"]), 34)
        self.assertEqual([row["id"] for row in binding["requirements"]], list(CUSTOM_COLLABORATION_REQUIREMENTS))
        self.assertEqual([row["id"] for row in binding["density_requirements"]], list(DENSITY_REQUIREMENTS))
        self.assertEqual(len(ATOMS), 34)
        self.assertEqual(len(density_requirements()), 34)
        self.assertEqual(len(parent_requirements()), 3)
        self.assertEqual(len(LOCAL_REQUIREMENTS), 37)
        self.assertEqual(list(REQUIREMENTS), list(LOCAL_REQUIREMENTS))
        plan = custom_collaboration_cli_case_plan()
        self.assertEqual(plan["requirement_ids"], list(REQUIREMENTS))
        self.assertEqual(custom_collaboration_cli_adaptability()["requirement_ids"], list(REQUIREMENTS))
        self.assertEqual(list(CUSTOM_COLLABORATION_REQUIREMENT_OPERATIONS), list(REQUIREMENTS))
        self.assertEqual(len(CUSTOM_COLLABORATION_COMMANDS), 3)
        self.assertEqual((plan["case_id"], plan["parent_id"]), (DUAL_CUSTOM_COLLAB_CASE_ID, PARENT))
        self.assertEqual(custom_collaboration_case_plan()["parent_requirement_count"], 37)

    def test_atom_operations_map_every_density_row_to_reviewed_leaves(self):
        method_to_operations = {method: operation for operation, method in CUSTOM_COLLABORATION_CASE_METHODS}
        rows = operator_source_binding()["density_requirements"]
        self.assertEqual([row["id"] for row in rows], list(DENSITY_REQUIREMENTS))
        for row in rows:
            operations = CUSTOM_COLLABORATION_REQUIREMENT_OPERATIONS[row["id"]]
            self.assertEqual(operations, (method_to_operations[row["method"]],), row["id"])
            for operation in operations:
                self.assertIn(operation, CUSTOM_COLLABORATION_CLI_OPERATIONS + tuple(DECLARED_CLI_EXIT_ROUTES))
        self.assertEqual(CUSTOM_COLLABORATION_REQUIREMENT_OPERATIONS[PREFIX + "trial-cleanup"], (GROUP_DELETE_OPERATION,))
        self.assertEqual(CUSTOM_COLLABORATION_REQUIREMENT_OPERATIONS[PREFIX + "persistent-cleanup"], (GROUP_DELETE_OPERATION,))
        trial_ops = CUSTOM_COLLABORATION_REQUIREMENT_OPERATIONS["CUSTOM-COLLABORATION.trial-one-shot-lifecycle"]
        self.assertIn(TRIAL_CREATE_GROUP_OPERATION, trial_ops)
        self.assertIn(RUN_CANCEL_OPERATION, trial_ops)
        create_ops = CUSTOM_COLLABORATION_REQUIREMENT_OPERATIONS["CUSTOM-COLLABORATION.create-persistent-group"]
        self.assertEqual(create_ops, (CREATE_PERSISTENT_GROUP_OPERATION, READ_PERSISTENT_GROUP_OPERATION,
                                      GROUP_DELETE_OPERATION, GROUP_VERIFY_DELETED_OPERATION))

    def test_density_rows_carry_live_cli_story_source_anchors(self):
        self.assertTrue(CLI_STORIES.is_file(), "cli-stories.sh must remain the frozen anchor source")
        lines = CLI_STORIES.read_text(encoding="utf-8").splitlines()
        rows = operator_source_binding()["density_requirements"]
        self.assertEqual([row["id"] for row in rows],
                         [PREFIX + name for name, _m, _f, _l in DENSITY_SPECS])
        methods = {name: method for name, method, _first, _last in DENSITY_SPECS}
        in_story_body = 0
        for row, (_name, _method, first, last) in zip(rows, DENSITY_SPECS):
            self.assertEqual(row["source_file"], "cli-stories.sh")
            self.assertEqual((row["line_start"], row["line_end"]), (first, last))
            self.assertEqual(row["method"], methods[row["id"][len(PREFIX):]])
            self.assertEqual(row["source_quote"], "\n".join(lines[first - 1:last]), row["id"])
            self.assertTrue(row["expectation"])
            self.assertTrue(row["command"])
            if first >= 119:
                in_story_body += 1
        # 33 anchors sit inside the story body (cli-stories.sh L119-278);
        # trial-group-id rides the shared _cli_story_create_pm_group helper
        # (L67) that this story itself invokes for the one-shot trial Group.
        self.assertEqual(in_story_body, 33)
        helper_row = next(row for row in rows if row["id"] == PREFIX + "trial-group-id")
        self.assertEqual((helper_row["line_start"], helper_row["line_end"]), (67, 67))
        self.assertIn("CLI-created group returns an id", helper_row["source_quote"])

    def test_density_source_drift_duplicates_and_short_rows_fail_closed(self):
        source = CLI_STORIES.read_text(encoding="utf-8")
        rows = operator_source_binding()["density_requirements"]
        validate_density_contract(rows, source)  # live frozen source stays green
        lines = source.splitlines()
        tampered = "\n".join(
            lines[:160]
            + ['    assert_json_eq "custom collaboration YAML is valid" '
               '"$BCS_CLI_STDOUT" "valid" "false"'] + lines[161:])
        with self.assertRaisesRegex(LocalCliError, "citation"):
            validate_density_contract(rows, tampered)
        duplicated = [dict(row) for row in rows]
        duplicated[1] = {**duplicated[1], "id": duplicated[0]["id"]}
        with self.assertRaisesRegex(LocalCliError, "citation"):
            validate_density_contract(duplicated, source)
        with self.assertRaisesRegex(LocalCliError, "incomplete"):
            validate_density_contract(rows[:-1], source)

    def test_local_plan_adaptability_and_density_delegation_are_locked(self):
        plan = custom_collaboration_case_plan()
        self.assertEqual((plan["case_id"], plan["legacy_id"], plan["parent_requirement_count"]),
                         (CASE_ID, "story_cli_operator_creates_custom_collaboration", 37))
        adaptability = custom_collaboration_case_adaptability()
        self.assertEqual(adaptability["local_executor_status"], "LOCAL_FULL_PARENT_EXECUTOR")
        self.assertEqual(adaptability["remote_validation"], "NOT_RUN")
        self.assertFalse(adaptability["grants_release_credit"])
        self.assertIn("preprod", adaptability["blocker"])
        # density=True delegates to the owned density executor; the flag is
        # consumed by the dispatcher and never forwarded as a journey input.
        sentinel = {"delegated": True}
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            with patch("bcs_e2e.custom_collaboration.owned.run_density_case",
                       return_value=sentinel) as delegated:
                result = run_custom_collaboration_case(
                    Mock(run_id="custom-collab"), state_dir=state_dir,
                    binary=Path(tmp) / "bcs-cli", binary_sha256="a" * 64,
                    yaml_path=state_dir / "definition.yaml", driver_id=DRIVER_BOT,
                    reviewer_id=MEMBER_BOT, human_id="human_" + HUMAN,
                    driver_token="owned-local-test-token", density=True)
        self.assertIs(result, sentinel)
        delegated.assert_called_once()
        self.assertNotIn("density", delegated.call_args.kwargs)
        self.assertEqual({"state_dir", "binary", "binary_sha256", "yaml_path", "driver_id",
                          "reviewer_id", "human_id", "driver_token", "timeout_seconds"},
                         set(delegated.call_args.kwargs))


class DensityProjectorTests(unittest.TestCase):
    def test_full_event_chain_projects_a_37_row_pass_receipt(self):
        receipt = project(full_events(), full_bindings(), full_base())
        self.assertEqual((receipt["status"], receipt["parsed_requirement_count"],
                          receipt["legacy_parent_count"], receipt["added_atomic_count"]),
                         ("PASS", 37, 3, 34))
        self.assertEqual(receipt["contract"], "custom-collaboration-density-v2")
        self.assertEqual(receipt["schema_version"], 1)
        self.assertEqual([row["id"] for row in receipt["requirements"]], list(LOCAL_REQUIREMENTS))
        self.assertTrue(all(row["status"] == "PASS" for row in receipt["requirements"]))
        for row in receipt["requirements"][:3]:
            self.assertEqual(row["evidence_event"], "legacy-parent")
        expected_events = {
            "yaml-valid": "validate", "yaml-role-count": "validate", "yaml-node-count": "validate",
            "yaml-planner-required": "definition", "yaml-reviewer-required": "definition",
            "yaml-plan-assignee": "definition", "yaml-review-assignee": "definition",
            "yaml-acyclic": "definition", "yaml-plan-review-edge": "definition",
            "yaml-review-final": "definition", "trial-group-id": "trial",
            "trial-session-id": "trial", "human-member": "member", "human-consultant": "member",
            "permission-allowed": "permission", "permission-session": "permission",
            "permission-group": "permission", "run-id": "run", "run-session": "run",
            "run-group": "run", "graph-run-id": "graph", "pending-human-empty": "pending",
            "respond-refused": "guard", "respond-not-pending": "guard",
            "panel-component": "panel", "panel-run": "panel", "cancel-terminal": "cancel",
            "session-running": "session", "persistent-id": "persistent",
            "persistent-driver": "persistent", "persistent-reviewer": "persistent",
            "persistent-strategy": "persistent_read", "trial-cleanup": "trial_deleted",
            "persistent-cleanup": "persistent_deleted"}
        self.assertEqual(len(expected_events), 34)
        for row in receipt["requirements"][3:]:
            atom = row["id"][len(PREFIX):]
            self.assertEqual(row["evidence_event"], expected_events[atom], atom)
        self.assertEqual(receipt["remote_validation"], "NOT_RUN")
        self.assertFalse(receipt["grants_release_credit"])
        self.assertEqual(receipt["presence"], "REMOTE_ZOMBIE_UNRESOLVED_NOT_PROBED")

    def test_missing_event_is_not_run_never_pass_and_blocks_the_receipt(self):
        events = {"validate": full_events()["validate"],
                  "definition": full_events()["definition"]}
        receipt = project(events, full_bindings(), full_base())
        statuses = {row["id"]: row["status"] for row in receipt["requirements"]}
        self.assertEqual(statuses[PREFIX + "yaml-valid"], "PASS")
        self.assertEqual(statuses[PREFIX + "yaml-planner-required"], "PASS")
        for atom in ("trial-group-id", "permission-session", "panel-run", "persistent-cleanup"):
            self.assertEqual(statuses[PREFIX + atom], "NOT_RUN", atom)
        self.assertEqual([row["status"] for row in receipt["requirements"][:3]],
                         ["PASS", "PASS", "PASS"])
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertTrue(verify(receipt))

    def test_failing_observation_is_fail_not_not_run(self):
        events = full_events()
        events["permission"] = {"allowed": False, "session_id": TRIAL_SESSION,
                                "group_id": TRIAL_GROUP}
        receipt = project(events, full_bindings(), full_base())
        statuses = {row["id"]: row["status"] for row in receipt["requirements"]}
        self.assertEqual(statuses[PREFIX + "permission-allowed"], "FAIL")
        self.assertEqual(statuses[PREFIX + "permission-session"], "PASS")
        self.assertEqual(statuses[PREFIX + "permission-group"], "PASS")
        self.assertEqual(receipt["status"], "BLOCKED")
        drifted = dict(events)
        drifted["validate"] = {"valid": True, "summary": {"participants": 3, "nodes": 2}}
        receipt2 = project(drifted, full_bindings(), full_base())
        self.assertEqual(next(row for row in receipt2["requirements"]
                              if row["id"] == PREFIX + "yaml-role-count")["status"], "FAIL")

    def test_parent_rows_ride_base_statuses_and_invalid_statuses_raise(self):
        base = {CUSTOM_COLLABORATION_REQUIREMENTS[0]: "FAIL"}
        receipt = project({}, {}, base)
        self.assertEqual([row["status"] for row in receipt["requirements"][:3]],
                         ["FAIL", "NOT_RUN", "NOT_RUN"])
        with self.assertRaisesRegex(RunStateError, "invalid status"):
            project(full_events(), full_bindings(),
                    {CUSTOM_COLLABORATION_REQUIREMENTS[1]: "MAYBE"})

    def test_definition_evidence_gates_syntax_atoms_on_the_frozen_fixture(self):
        evidence = definition_evidence(DEFINITION_YAML)
        self.assertTrue(evidence["canonical"])
        self.assertTrue(all(evidence["clauses"].values()))
        self.assertEqual(len(evidence["clauses"]), 7)
        drift = definition_evidence("name: drifted fixture\n")
        self.assertFalse(drift["canonical"])
        self.assertFalse(any(drift["clauses"].values()))
        # A drifted definition FAILs the six definition-clause atoms; it never
        # silently passes them and never crashes the projector.
        events = full_events()
        events["definition"] = drift
        receipt = project(events, full_bindings(), full_base())
        self.assertEqual(next(row for row in receipt["requirements"]
                              if row["id"] == PREFIX + "yaml-acyclic")["status"], "FAIL")

    def test_panel_evidence_requires_the_marker_and_the_bound_run(self):
        history = [{"content": '<AixUI type="panel" '
                               'component="bcsPanel.StateMachineRunView" '
                               'params=\'{"runId": "run-1"}\'/>'}]
        bound = panel_evidence(history, "run-1")
        self.assertTrue(bound["panel_present"])
        self.assertTrue(bound["panel_run_bound"])
        self.assertFalse(panel_evidence(history, "run-other")["panel_run_bound"])
        self.assertFalse(panel_evidence([{"content": "plain chat text"}], "run-1")["panel_present"])

    def test_verify_roundtrip_rejects_tamper_and_malformed_receipts(self):
        receipt = project(full_events(), full_bindings(), full_base())
        self.assertTrue(verify(receipt))
        tampered = json.loads(json.dumps(receipt))
        tampered["requirements"][3]["status"] = "FAIL"
        self.assertFalse(verify(tampered))
        for broken in ({"requirements": []}, {"requirements": "x"},
                       {**receipt, "observations": None}):
            self.assertFalse(verify(broken))


class LocalEnvelopeProjectorTests(unittest.TestCase):
    def test_project_density_fail_closes_on_absent_and_wrong_fields(self):
        checks = {
            "yaml-valid": project_density("validate_yaml", {})["CUSTOM-COLLABORATION.yaml-valid"],
            "yaml-role-count": project_density(
                "validate_yaml", {"valid": True, "summary": {"participants": 3, "nodes": 2}})["CUSTOM-COLLABORATION.yaml-role-count"],
            "yaml-node-count": project_density(
                "validate_yaml", {"valid": True, "summary": {"participants": 2}})["CUSTOM-COLLABORATION.yaml-node-count"],
            "yaml-review-final": project_density(
                "validate_yaml",
                {"valid": True, "summary": {"participants": 2, "nodes": 2,
                                            "final_output_node": "plan"},
                 "participants": [], "graph": {"nodes": [
                     {"node_id": "plan", "kind": "bot_task",
                      "assignee": {"type": "bot_binding", "binding": "planner"}},
                     {"node_id": "review", "kind": "bot_task",
                      "assignee": {"type": "bot_binding", "binding": "reviewer"}}],
                     "graph_mode": "acyclic", "edges": []}})["CUSTOM-COLLABORATION.yaml-review-final"],
            "trial-group-id": project_density("trial_create_group", {"session_id": "s"})["CUSTOM-COLLABORATION.trial-group-id"],
            "trial-session-id": project_density("trial_create_group", {"id": "g"})["CUSTOM-COLLABORATION.trial-session-id"],
            "human-member": project_density(
                "trial_add_member",
                {"participants": [{"bot_uuid": "human_9", "role": "consultant"}]},
                session_id="s1", human_id="human_9")["CUSTOM-COLLABORATION.human-member"],
            "human-consultant": project_density(
                "trial_add_member",
                {"session_id": "s1", "participants": [{"bot_uuid": "human_9", "role": "planner"}]},
                session_id="s1", human_id="human_9")["CUSTOM-COLLABORATION.human-consultant"],
            "permission-allowed": project_density(
                "trial_permission", {"allowed": False})["CUSTOM-COLLABORATION.permission-allowed"],
            "run-id": project_density(
                "trial_start_run", {"run": {"session_id": "s", "group_id": "g"}})["CUSTOM-COLLABORATION.run-id"],
            "graph-run-id": project_density(
                "trial_query_graph", {"run": {"run_id": "other"}}, run_id="r1")["CUSTOM-COLLABORATION.graph-run-id"],
            "pending-human-empty": project_density(
                "trial_query_pending", [{"node": "plan"}])["CUSTOM-COLLABORATION.pending-human-empty"],
            "respond-refused": project_density(
                "trial_respond_guard", {"not_pending": True})["CUSTOM-COLLABORATION.respond-refused"],
            "respond-not-pending": project_density(
                "trial_respond_guard", {"guard_refused": True})["CUSTOM-COLLABORATION.respond-not-pending"],
            "cancel-terminal": project_density(
                "cancel_trial_run", {"run": {"run_id": "r1", "status": "running"}},
                run_id="r1")["CUSTOM-COLLABORATION.cancel-terminal"],
            "session-running": project_density(
                "read_trial_session",
                {"session_id": "s1", "group_id": "g1", "status": "completed"},
                session_id="s1", group_id="g1")["CUSTOM-COLLABORATION.session-running"],
            "persistent-id": project_density(
                "create_persistent_group", {"participants": []},
                driver_id="d1", reviewer_id="m1")["CUSTOM-COLLABORATION.persistent-id"],
            "persistent-driver": project_density(
                "create_persistent_group", {"id": "g2", "participants": ["m1"]},
                driver_id="d1", reviewer_id="m1")["CUSTOM-COLLABORATION.persistent-driver"],
            "persistent-strategy": project_density(
                "read_persistent_group", {"id": "g2", "group_strategy": "chat"},
                group_id="g2")["CUSTOM-COLLABORATION.persistent-strategy"],
            "trial-cleanup": project_density(
                "delete_trial_group", {"id": "g1", "deleted": False},
                group_id="g1")["CUSTOM-COLLABORATION.trial-cleanup"],
            "persistent-cleanup": project_density(
                "delete_persistent_group", {"id": "g2", "deleted": False},
                group_id="g2")["CUSTOM-COLLABORATION.persistent-cleanup"],
        }
        for atom, value in checks.items():
            self.assertIs(value, False, atom)
        # Persistent participants bind in both the string and row forms.
        string_form = project_density("create_persistent_group",
                                      {"id": "g2", "participants": ["d1", "m1"]},
                                      driver_id="d1", reviewer_id="m1")
        row_form = project_density("create_persistent_group",
                                   {"id": "g2", "participants": [{"bot_uuid": "d1"}, {"bot_uuid": "m1"}]},
                                   driver_id="d1", reviewer_id="m1")
        for form in (string_form, row_form):
            self.assertTrue(form["CUSTOM-COLLABORATION.persistent-driver"])
            self.assertTrue(form["CUSTOM-COLLABORATION.persistent-reviewer"])

    def test_project_density_parses_the_real_aixui_panel_marker(self):
        xml = ('<AixUI type="panel" component="bcsPanel.StateMachineRunView" '
               'params=\'{"runId": "run-1"}\'/>')
        good = project_density("trial_messages", [{"content": xml}], run_id="run-1")
        self.assertTrue(good["CUSTOM-COLLABORATION.panel-component"])
        self.assertTrue(good["CUSTOM-COLLABORATION.panel-run"])
        wrong_run = project_density("trial_messages", [{"content": xml}], run_id="run-other")
        self.assertTrue(wrong_run["CUSTOM-COLLABORATION.panel-component"])
        self.assertFalse(wrong_run["CUSTOM-COLLABORATION.panel-run"])
        plain = project_density("trial_messages", [{"content": "plain text"}], run_id="run-1")
        self.assertFalse(plain["CUSTOM-COLLABORATION.panel-component"])

    def test_density_assertions_map_observations_without_ever_fabricating_pass(self):
        observed = {PREFIX + name: True for name in ATOMS}
        assertions = density_assertions(observed)
        self.assertEqual([row["id"] for row in assertions], list(DENSITY_REQUIREMENTS))
        self.assertTrue(all(row["status"] == "PASS" for row in assertions))
        partial = {PREFIX + "yaml-valid": True}
        self.assertEqual([row["status"] for row in density_assertions(partial)][1:], ["NOT_RUN"] * 33)
        self.assertEqual(density_assertions({PREFIX + "yaml-valid": False})[0]["status"], "NOT_RUN")


class DualEnvelopeDensityTests(unittest.TestCase):
    def setUp(self):
        FakeWriteClient.instances.clear()
        FakeWriteClient.failed = False

    def test_dual_pass_attaches_all_34_density_steps_with_source_rows(self):
        adapter, runner, execution = dual_chain()
        self.assertEqual((execution.status, execution.completed_count, execution.total_count),
                         ("PASS", 37, 37))
        self.assertTrue(execution.ready_to_execute)
        parents, density = execution.steps[:3], execution.steps[3:]
        self.assertEqual([step.requirement_id for step in parents],
                         [REQUIREMENTS[0], REQUIREMENTS[2], REQUIREMENTS[1]])
        self.assertEqual([step.requirement_id for step in density],
                         [PREFIX + name for name, _m, _f, _l in DENSITY_SPECS])
        self.assertEqual([step.method for step in density],
                         [method for _name, method, _first, _last in DENSITY_SPECS])
        self.assertEqual({step.requirement_id for step in execution.steps}, set(REQUIREMENTS))
        for step in density:
            self.assertEqual(step.status, "PASS")
            self.assertIsNone(step.error_type)
            row = step.data
            self.assertEqual(row["status"], "PASS")
            self.assertEqual(row["source_file"], "cli-stories.sh")
            self.assertTrue(row["source_quote"])
            self.assertTrue(row["expectation"])
            self.assertTrue(row["command"])
            self.assertIsInstance(row["line_start"], int)
            self.assertIn(row["evidence_event"],
                          {"validate", "definition", "trial", "member", "permission", "run",
                           "graph", "pending", "guard", "panel", "cancel", "session",
                           "trial_deleted", "persistent", "persistent_read", "persistent_deleted"})
            self.assertEqual(len(step.evidence["requests"]), 1)
            self.assertFalse(step.evidence["requests"][0]["grants_release_credit"])
        effects = {step.method: step.evidence["requests"][0]["fixture_effect"]
                   for step in density
                   if step.method in ("delete_trial_group", "delete_persistent_group")}
        self.assertEqual(len(effects), 2)
        self.assertNotEqual(*effects.values())
        # The product-CLI runner only ever drives the eleven reviewed leaves;
        # the five HTTP exits ride the patched cleanup WriteClient.
        self.assertEqual([call[0] for call in runner.calls], list(CUSTOM_COLLABORATION_CLI_OPERATIONS))
        custom_collaboration_source_binding()  # live frozen-source gate stays green

    def test_dual_nonzero_exit_fail_close_names_the_product_gap(self):
        # Live r5/r6 station defect: the trial Group creation leaf exits
        # nonzero on the product CLI face.  The envelope must fail closed with
        # the named scrubbed class and NOT_RUN everywhere - never a PASS.
        failure = CustomCollaborationCliRunnerError(
            "custom collaboration product CLI returned nonzero: trial create-group refused")
        for preprod in (False, True):
            with self.subTest(tier="preprod" if preprod else "local"):
                adapter, _runner, execution = dual_chain(
                    preprod=preprod, fail_at=TRIAL_CREATE_GROUP_OPERATION, failure=failure)
                self.assertEqual((execution.status, execution.completed_count,
                                  execution.total_count, execution.error_type),
                                 ("BLOCKED", 0, 37, "RunStateError"))
                self.assertEqual(adapter.failed_method, "trial_create_group")
                self.assertEqual(adapter.failure_class, "PRODUCT_CLI_NONZERO_EXIT")
                for step in execution.steps:
                    self.assertEqual(step.status, "NOT_RUN")
                    self.assertEqual(step.error_gap, "PRODUCT_CLI_NONZERO_EXIT")
                self.assertIsInstance(execution.error_detail, str)
                snapshot = json.dumps(execution.snapshot())
                self.assertNotIn("20261026_xq102n2b", snapshot)

    def test_dual_distinct_public_failure_classes_never_silently_repair(self):
        # Transport failures, projection drift and nonzero exits each keep
        # their own scrubbed class on every blocked step.
        FakeWriteClient.failed = True
        try:
            adapter, _runner, transport = dual_chain()
        finally:
            FakeWriteClient.failed = False
        self.assertEqual(transport.status, "BLOCKED")
        self.assertEqual(transport.error_type, "WriteTransportError")
        self.assertEqual(adapter.failure_class, "TRANSPORT_HTTP_ERROR")
        self.assertEqual({step.error_gap for step in transport.steps}, {"TRANSPORT_HTTP_ERROR"})
        _adapter2, _runner2, drifted = dual_chain(fail_at=TRIAL_MESSAGES_OPERATION)
        self.assertEqual(drifted.error_type, "RunStateError")
        self.assertEqual(_adapter2.failure_class, "CASE_CONTRACT_VIOLATION")
        self.assertEqual({step.error_gap for step in drifted.steps}, {"CASE_CONTRACT_VIOLATION"})
        for step in drifted.steps[3:]:
            self.assertEqual(step.error_type, "RunStateError")
            self.assertEqual(step.status, "NOT_RUN")

    def test_dual_profile_tiers_and_credential_families_gate_the_envelope(self):
        local = validate_custom_collaboration_cli_profile(profile_value())
        self.assertEqual(local["environment"]["tier"], "local")
        self.assertNotIn("browser_origin", local)
        preprod = validate_custom_collaboration_cli_profile(profile_value(preprod=True))
        self.assertEqual(preprod["environment"]["tier"], "preprod")
        self.assertEqual(preprod["browser_origin"], "https://teamclaw-pre.alipay.com")
        self.assertEqual(preprod["entrypoint"]["origin"], "https://bcs-pre.alipay.com")
        for profile in (local, preprod):
            for operation in (VALIDATE_YAML_OPERATION, TRIAL_CREATE_GROUP_OPERATION,
                              RUN_CANCEL_OPERATION):
                label = custom_collaboration_step_credential_kind(profile, operation)
                self.assertIn(label, {"none", "owned_local_bot_token", "human_cookie"})
        with self.assertRaisesRegex(ConfigError, "tier"):
            validate_custom_collaboration_cli_profile(
                profile_value(mutate=lambda v: v["environment"].update({"tier": "staging"})))
        with self.assertRaisesRegex(ConfigError, "loopback"):
            validate_custom_collaboration_cli_profile(
                profile_value(mutate=lambda v: v["entrypoint"].update(
                    {"origin": "https://127.0.0.1:21000"})))
        for operation in (RUN_CANCEL_OPERATION, SESSION_READ_OPERATION,
                          GROUP_DELETE_OPERATION, GROUP_VERIFY_DELETED_OPERATION):
            self.assertEqual(expected_step_transport(operation), "PRODUCT_HTTP")
        self.assertTrue(all(expected_step_transport(op) == "PRODUCT_CLI"
                            for op in CUSTOM_COLLABORATION_CLI_OPERATIONS))
        self.assertEqual(sorted(SCENARIO_CLEANUP_OPERATIONS),
                         sorted({GROUP_DELETE_OPERATION, GROUP_VERIFY_DELETED_OPERATION}))


if __name__ == "__main__":
    unittest.main()
