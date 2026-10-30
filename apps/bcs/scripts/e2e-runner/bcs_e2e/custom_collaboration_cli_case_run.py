"""Command entry for the reviewed dual Custom-collaboration product-CLI case.

The three reviewed obligations (``CUSTOM-COLLABORATION.validate-yaml``,
``CUSTOM-COLLABORATION.create-persistent-group`` and
``CUSTOM-COLLABORATION.trial-one-shot-lifecycle``) own an exact
owned-local parent executor (``local_custom_collaboration_case``) frozen by
``custom-collaboration-migration-contract.json``.  This module re-chains
that exact parent through the product ``bcs-cli`` collaboration leaves and
admits both the owned Singlebox and the preprod gateway faces.  Conservative
notes, never silently repaired:

- The trial Run-cancel route (``POST /state-machine-runs/{run_id}/cancel``)
  is not expressible through the cleanup ``WriteClient.call`` placeholder
  vocabulary, so the cancel step derives a per-run cleanup Profile with the
  literal quoted path; the frozen Profile keeps the contract shape.
- The trial obligation rides the session-domain product CLI exactly as the
  frozen owned-local parent (S14 dual-write arbitration, 2026-10-30): the
  persistent-form trial Group is created through the reviewed create leaf, the
  Human consultant membership, the trial authority and the one-shot Run start
  (``collaborate run <yaml> --session <id> --binding planner=<driver>
  --binding reviewer=<member> --input <TRIAL_INPUT>``) are re-chained onto
  their upstream-anchored session-domain leaves, and the run-domain reads, the
  respond guard, the cancel and the exact owned delete follow. The raw
  group-domain HTTP Run start remains only as a declared fallback route
  binding (the frozen Profile keeps its reviewed contract shape) and is never
  executed on either tier.
- Blocked runs project one scrubbed public failure class (receipt keys
  ``failed_method``/``failure_class`` plus every frozen step's ``error_gap``;
  live r1-r4 showed all-first-step NOT_RUN blocks whose failing leaf was
  indistinguishable after credential scrubbing). The projection carries no
  host, route, payload or credential detail - only the class of the failure
  that stopped the chain - so intermittent remote conditions stay
  diagnosable per run without widening the frozen obligations.
- Every cleanup exit runs under the dedicated driver Bot identity (the
  owned-local parent never mutates through the member Bot), so the preprod
  bundle is the Human Cookie plus the owned driver Bot token.
- YAML validation is unauthenticated at the BCS application layer, but the
  station gateway identity-gates the route on remote tiers (live r1/r2:
  anonymous and driver-token requests are refused with USER_NOT_LOGIN while
  the Human Cookie identity is accepted). The owned Singlebox preflight keeps
  the anonymous ``none`` label; the remote validation leaf presents the
  family credential.
"""

from __future__ import annotations

from pathlib import Path
import json
import re

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .custom_collaboration_cli_leaf import (
    CUSTOM_COLLABORATION_CASE_METHODS,
    CUSTOM_COLLABORATION_CLI_EFFECT,
    CUSTOM_COLLABORATION_CLI_OPERATIONS,
    CUSTOM_COLLABORATION_CLI_PROFILE_CONTRACT,
    CUSTOM_COLLABORATION_CLI_RELATIONSHIPS,
    CUSTOM_COLLABORATION_CREATE_EFFECT,
    CUSTOM_COLLABORATION_REQUIREMENT_OPERATIONS,
    CUSTOM_COLLABORATION_TRIAL_EFFECT,
    CUSTOM_COLLABORATION_VALIDATE_EFFECT,
    CREATE_PERSISTENT_GROUP_OPERATION,
    DECLARED_CLEANUP_ROUTES,
    GROUP_DELETE_OPERATION,
    GROUP_VERIFY_DELETED_OPERATION,
    READ_PERSISTENT_GROUP_OPERATION,
    REQUIREMENTS as CUSTOM_COLLABORATION_REQUIREMENTS,
    RUN_CANCEL_OPERATION,
    SCENARIO_CLEANUP_OPERATIONS,
    SESSION_READ_OPERATION,
    _HTTP_PRODUCT_OPERATIONS,
    _HTTP_SCENARIO_OPERATIONS,
    _STEP_EFFECTS,
    TRIAL_ADD_MEMBER_OPERATION,
    TRIAL_CREATE_GROUP_OPERATION,
    TRIAL_PERMISSION_OPERATION,
    TRIAL_START_RUN_OPERATION,
    TRIAL_MESSAGES_OPERATION,
    TRIAL_QUERY_GRAPH_OPERATION,
    TRIAL_QUERY_PENDING_OPERATION,
    TRIAL_RESPOND_GUARD_OPERATION,
    VALIDATE_YAML_OPERATION,
    CustomCollaborationCliRunner,
    CustomCollaborationCliRunnerError,
    custom_collaboration_cli_credential_kind,
    custom_collaboration_step_credential_kind,
    expected_step_transport,
    participant_ids,
    validate_custom_collaboration_cli_profile,
)
from .custom_collaboration.density import (
    definition_evidence,
    panel_evidence,
    project,
)
from .inventory import executor_digest
from .local_custom_collaboration_case import (
    CUSTOM_COLLABORATION_PARENT,
    custom_collaboration_case_plan,
    custom_collaboration_source_binding,
)
from .local_custom_collaboration_run import DEFINITION_YAML
from .local_operator_cli_contract import (
    CUSTOM_COLLABORATION_COMMANDS,
    custom_collaboration_source_binding as operator_source_binding,
)
from .profile import ConfigError, fingerprint, identifier, require_resolved, unique_keys
from .run_state import RunStateError, write
from .workspace_cli_commands import WorkspaceCliRunnerError
from .write_transport import WriteClient, WriteTransportError

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-custom-collaboration-case-run-v2"
PROFILE_FILE = "custom-collaboration-profile.json"
BINDING_FILE = "custom-collaboration-binding.json"
REPORT_FILE = "custom-collaboration-run.json"
DEFINITION_FILE = "custom-collaboration-definition.yaml"
CASE_KEYS = ("custom-collaboration",)
CASE_COMMAND = "dual-custom-collaboration-run"
DUAL_CUSTOM_COLLAB_CASE_ID = "DUAL-CUSTOM-COLLAB-01"
REQUIREMENTS = CUSTOM_COLLABORATION_REQUIREMENTS
PARENT = CUSTOM_COLLABORATION_PARENT

# Case-owned path segments are frozen to the reviewed identifier charset, so
# the guarded Case executor reproduces the transport's default percent-encoding
# with an explicit table instead of importing the raw URL machinery.
_UNRESERVED_PATH_SEGMENT = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_PATH_SEGMENT_ENCODING = {":": "%3A", "@": "%40"}


def _quote_path_segment(value: str) -> str:
    """Percent-encode one Case-owned path segment as the transport would."""
    quoted = []
    for char in value:
        if char in _UNRESERVED_PATH_SEGMENT:
            quoted.append(char)
        elif char in _PATH_SEGMENT_ENCODING:
            quoted.append(_PATH_SEGMENT_ENCODING[char])
        else:
            raise RunStateError(
                "custom collaboration path segment carries a character outside "
                "the reviewed identifier contract")
    return "".join(quoted)


PUBLIC_FAILURE_CLASS_MARKER = "public-failure-class:"
CLI_FAILURE_CLASS_MARKERS = (
    ("product CLI returned nonzero", "PRODUCT_CLI_NONZERO_EXIT"),
    ("product CLI process failed", "PRODUCT_CLI_PROCESS_FAILED"),
    ("product CLI output is invalid", "PRODUCT_CLI_OUTPUT_INVALID"),
    ("private context cleanup is unconfirmed", "PRODUCT_CLI_CLEANUP_UNCONFIRMED"),
    ("credential", "PRODUCT_CLI_CREDENTIALS_ABSENT"),
    ("bundle (owned driver Bot token plus the Human Cookie) is absent",
     "PRODUCT_CLI_CREDENTIALS_ABSENT"),
)


def public_failure_class(error: BaseException) -> str:
    """Project one scrubbed public failure class for a blocked Run.

    Live preprod r1-r4 receipts were all-first-step NOT_RUN blocks whose
    failing leaf and public failure class were indistinguishable after
    credential scrubbing.  The projection carries no host, route, payload or
    credential detail - only the class of the failure that stopped the chain -
    so intermittent remote conditions remain disclosable per run without
    widening the frozen obligations."""
    if isinstance(error, WriteTransportError):
        return "TRANSPORT_HTTP_ERROR"
    text = str(error)
    for marker, name in CLI_FAILURE_CLASS_MARKERS:
        if marker in text:
            return name
    for match in re.finditer(
            re.escape(PUBLIC_FAILURE_CLASS_MARKER) + r"([A-Z_]+)", text):
        return match.group(1)
    return "CASE_CONTRACT_VIOLATION"


def custom_collaboration_cli_case_plan() -> dict:
    """Freeze the dual Custom-collaboration plan from its reviewed source."""
    binding = custom_collaboration_source_binding()
    plan = custom_collaboration_case_plan()
    operator_binding = operator_source_binding()
    base_commands: list[str] = []
    for row in operator_binding["requirements"]:
        if row["command"] not in base_commands:
            base_commands.append(row["command"])
    if base_commands != list(CUSTOM_COLLABORATION_COMMANDS):
        raise RunStateError("custom collaboration base source commands changed")
    requirement_ids = [row["id"] for row in binding["requirements"]]
    if (binding["parent"] != PARENT or requirement_ids != list(REQUIREMENTS)
            or plan["requirement_ids"] != list(REQUIREMENTS) or plan["legacy_id"] != PARENT
            or tuple(CUSTOM_COLLABORATION_REQUIREMENT_OPERATIONS) != tuple(REQUIREMENTS)
            or len({method for _, method in CUSTOM_COLLABORATION_CASE_METHODS})
            != len(CUSTOM_COLLABORATION_CASE_METHODS)
            or {op for op, _ in CUSTOM_COLLABORATION_CASE_METHODS}
            != (set(CUSTOM_COLLABORATION_CLI_OPERATIONS) | set(_HTTP_PRODUCT_OPERATIONS)
                | set(_HTTP_SCENARIO_OPERATIONS))):
        raise RunStateError("custom collaboration source obligation map changed")
    return {
        "case_id": DUAL_CUSTOM_COLLAB_CASE_ID, "parent_id": PARENT,
        "requirement_ids": requirement_ids,
        "adapter_methods": [method for _, method in CUSTOM_COLLABORATION_CASE_METHODS],
        "cli_operations": list(CUSTOM_COLLABORATION_CLI_OPERATIONS),
        "cleanup_operations": sorted(_HTTP_PRODUCT_OPERATIONS),
        "scenario_http_operations": sorted(_HTTP_SCENARIO_OPERATIONS),
        "source_commands": base_commands,
        "source_expectations": [row["expectation"] for row in operator_binding["requirements"]],
    }


def custom_collaboration_cli_adaptability() -> dict:
    plan = custom_collaboration_cli_case_plan()
    return {
        "case_id": DUAL_CUSTOM_COLLAB_CASE_ID, "legacy_id": plan["parent_id"],
        "parent_id": plan["parent_id"],
        "requirement_ids": plan["requirement_ids"],
        "selected_obligations": ["cli-collaboration-validate",
                                 "cli-collaboration-trial",
                                 "cli-collaboration-create-persistent-group"],
        "cli_operations": plan["cli_operations"],
        "cleanup_operations": plan["cleanup_operations"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_PARENT_EXECUTOR",
        "cli_surface": "product-bcs-cli-collaboration",
        "remote_validation": "NOT_RUN", "grants_release_credit": False,
        "blocker": (
            "Mock-verified Singlebox and preprod product-CLI contexts through the "
            "reviewed collaboration leaves, including the upstream-anchored "
            "session-domain membership, authority and one-shot Run-start leaves. "
            "The trial obligation rides the session-domain product CLI exactly as "
            "the frozen owned-local parent (create consultative Group, add the "
            "Human consultant, grant trial authority, start the one-shot Run "
            "through collaborate run, read the graph and pending Human inputs, "
            "prove the respond guard refusal, restore the AixUI history, cancel "
            "the Run, preserve the consultant Session running, then delete both "
            "exact Groups with 404 proof); the raw group-domain HTTP Run start "
            "stays a declared fallback route binding and is never executed. "
            "Real preprod Bot runtimes did not consume the bot_task node inside "
            "the live 75s window (plan node awaiting_response until cancel); "
            "consumption is disclosed as a live condition, never asserted. Live "
            "effect admission and real preprod credentials remain separate gates."),
    }


class RemoteLocalCustomCollaborationAdapter:
    """Chain the reviewed obligations and project exact case evidence."""

    _profile_validator = staticmethod(validate_custom_collaboration_cli_profile)

    def __init__(self, profile: dict, runner: object):
        self.profile = self._profile_validator(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("Custom collaboration requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.human_id = self.profile["human"]["user_id"]
        self.trial_group_id: str | None = None
        self.trial_group_session_handle: str | None = None
        self.trial_session_id: str | None = None
        self.trial_run_id: str | None = None
        self.persistent_group_id: str | None = None
        self.fixture_effect = CUSTOM_COLLABORATION_CLI_EFFECT
        self.cleanup_client = WriteClient(self._cleanup_profile(self.profile))

    @staticmethod
    def _cleanup_profile(profile: dict) -> dict:
        value = json.loads(json.dumps(profile, allow_nan=False))
        if profile["environment"]["tier"] == "local":
            value["auth"] = {"kind": "authenticated-product",
                             "header_env": {"Authorization":
                                            profile["auth"]["driver_token_env"]}}
        else:
            value["auth"] = {"kind": "human-cookie",
                             "header_env": {"Cookie": profile["auth"]["cookie_env"]}}
        value["entrypoint"] = {"kind": profile["entrypoint"]["kind"],
                               "origin": profile["entrypoint"]["origin"],
                               "api_face": "legacy", "manifest_face": "legacy"}
        return value

    @property
    def human_actor_id(self) -> str:
        """Upstream identifier form of the Human consultant (``human_<id>``)."""
        return "human_" + self.human_id

    def _credential_kind(self) -> str:
        return custom_collaboration_cli_credential_kind(self.profile)

    def _cli(self, operation: str, *, case_class: str, **kwargs) -> AdapterCall:
        try:
            result = self.runner.execute(operation, **kwargs)
        except Exception as error:
            raise RunStateError(
                f"Custom collaboration product-CLI execution for {operation} failed"
                f" [{PUBLIC_FAILURE_CLASS_MARKER}"
                f"{public_failure_class(error)}]"
            ) from error
        if (not isinstance(result, dict) or set(result) != {"data", "evidence"}
                or not isinstance(result["evidence"], dict)):
            raise RunStateError(f"Custom collaboration CLI result for {operation} is invalid")
        evidence = dict(result["evidence"])
        evidence.update({"case_class": case_class,
                         "fixture_effect": _STEP_EFFECTS[operation],
                         "remote_validation": "NOT_RUN", "grants_release_credit": False})
        return AdapterCall(data=result["data"], evidence=evidence)

    def _http(self, operation: str, *, case_class: str, group_id: str = "",
              session_id: str = "", body: dict | None = None,
              expected: tuple[int, ...] = (200,), parse_error: bool = False) -> AdapterCall:
        client = self.cleanup_client
        if operation == RUN_CANCEL_OPERATION:
            client = WriteClient(self._cancel_profile(self.trial_run_id))
        data, record = client.call(
            operation, group_id=group_id, session_id=session_id, body=body,
            expected=expected, parse_error=parse_error)
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP", "caller_role": "case_owned_driver",
            "credential_kind": ("owned_local_bot_token"
                                if self.profile["environment"]["tier"] == "local"
                                else "human_cookie"),
            "case_class": case_class,
            "fixture_effect": (CUSTOM_COLLABORATION_CREATE_EFFECT
                               if case_class.startswith("persistent")
                               else CUSTOM_COLLABORATION_TRIAL_EFFECT),
            "remote_validation": "NOT_RUN", "grants_release_credit": False})
        return AdapterCall(data=data, evidence=evidence)

    def _cancel_profile(self, run_id: object) -> dict:
        identifier(run_id, "custom collaboration Run ID")
        value = json.loads(json.dumps(self.cleanup_client.profile, allow_nan=False))
        binding = dict(value["route_bindings"][RUN_CANCEL_OPERATION])
        binding["path"] = f"/state-machine-runs/{_quote_path_segment(str(run_id))}/cancel"
        value["route_bindings"][RUN_CANCEL_OPERATION] = binding
        return value

    def validate_yaml(self) -> AdapterCall:
        call = self._cli(VALIDATE_YAML_OPERATION, case_class="validate-yaml",
                         yaml_path=self.runner.definition_path)
        data = call.data
        if (not isinstance(data, dict) or data.get("valid") is not True
                or not isinstance(data.get("summary"), dict)
                or data["summary"].get("participants") != 2
                or data["summary"].get("nodes") != 2):
            raise RunStateError("custom collaboration YAML validation differs")
        return call

    def trial_create_group(self) -> AdapterCall:
        if self.trial_group_id is not None:
            raise RunStateError("Custom collaboration trial Group creation is one-shot")
        call = self._cli(TRIAL_CREATE_GROUP_OPERATION, case_class="trial-create-group",
                         yaml_path=self.runner.definition_path)
        data = call.data
        if (not isinstance(data, dict) or not data.get("id")
                or not data.get("session_id")
                or data.get("driver_bot") != self.driver_id
                or data.get("created") is not True
                or len(data.get("participants") or []) != 2
                or set(participant_ids(data.get("participants")))
                != {self.driver_id, self.member_id}):
            raise RunStateError("custom collaboration trial Group differs")
        # The frozen owned-local parent binds the whole trial lifecycle to
        # this create-bound consultant Session (local_cli._parse_trial_group).
        # The remote convergence keeps that binding and derives the
        # run-scoped Session separately when the group-domain Run starts.
        self.trial_group_id, self.trial_group_session_handle = (
            data["id"], data["session_id"])
        self.trial_session_id = data["session_id"]
        return call

    def trial_add_member(self) -> AdapterCall:
        if self.trial_session_id is None:
            raise RunStateError("Custom collaboration trial membership requires its Session")
        call = self._cli(TRIAL_ADD_MEMBER_OPERATION, case_class="trial-add-member",
                         session_id=self.trial_session_id, human_id=self.human_actor_id)
        data = call.data
        rows = data.get("participants") if isinstance(data, dict) else None
        if (not isinstance(rows, list)
                or (data.get("session_id") or data.get("id")) != self.trial_session_id
                or not any(isinstance(row, dict)
                           and (row.get("bot_uuid") or row.get("actor_id")) == self.human_actor_id
                           and row.get("role") == "consultant" for row in rows)):
            raise RunStateError("custom collaboration trial membership differs")
        return call

    def trial_permission(self) -> AdapterCall:
        if self.trial_session_id is None or self.trial_group_id is None:
            raise RunStateError("Custom collaboration trial authority requires its identities")
        call = self._cli(TRIAL_PERMISSION_OPERATION, case_class="trial-permission",
                         session_id=self.trial_session_id)
        data = call.data
        if (not isinstance(data, dict) or data.get("allowed") is not True
                or (data.get("session_id") or data.get("id")) != self.trial_session_id
                or data.get("group_id") != self.trial_group_id
                or data.get("policy_version") != "session_state_machine_v1"
                or data.get("group_strategy") != "chat"
                or data.get("caller_bot_id") != self.driver_id):
            raise RunStateError("custom collaboration trial authority differs")
        return call

    def trial_start_run(self) -> AdapterCall:
        if (self.trial_group_id is None or self.trial_session_id is None
                or self.trial_run_id is not None):
            raise RunStateError("Custom collaboration one-shot Run requires its Group")
        call = self._cli(TRIAL_START_RUN_OPERATION, case_class="trial-run-start",
                         yaml_path=self.runner.definition_path,
                         session_id=self.trial_session_id)
        run = call.data.get("run") if isinstance(call.data, dict) else None
        if (not isinstance(run, dict) or not run.get("run_id")
                or run.get("session_id") != self.trial_session_id
                or run.get("group_id") != self.trial_group_id
                or run.get("status") not in ("running", "waiting")):
            raise RunStateError("custom collaboration session-domain Run start differs")
        self.trial_run_id = run["run_id"]
        return call

    def trial_query_graph(self) -> AdapterCall:
        if self.trial_run_id is None:
            raise RunStateError("custom collaboration graph read requires its Run")
        call = self._cli(TRIAL_QUERY_GRAPH_OPERATION, case_class="trial-query-graph",
                         run_id=self.trial_run_id)
        data = call.data
        run = data.get("run") if isinstance(data, dict) else None
        if (not isinstance(run, dict) or run.get("run_id") != self.trial_run_id
                or run.get("session_id") != self.trial_session_id
                or run.get("group_id") != self.trial_group_id
                or not isinstance(data.get("nodes"), list) or not data["nodes"]):
            raise RunStateError("custom collaboration execution graph differs")
        return call

    def trial_query_pending(self) -> AdapterCall:
        if self.trial_run_id is None:
            raise RunStateError("custom collaboration pending read requires its Run")
        call = self._cli(TRIAL_QUERY_PENDING_OPERATION, case_class="trial-query-pending",
                         run_id=self.trial_run_id)
        if call.data != []:
            raise RunStateError("custom collaboration pending Human inputs differ")
        return call

    def trial_respond_guard(self) -> AdapterCall:
        if self.trial_run_id is None:
            raise RunStateError("custom collaboration respond guard requires its Run")
        call = self._cli(TRIAL_RESPOND_GUARD_OPERATION, case_class="trial-respond-guard",
                         run_id=self.trial_run_id)
        if not isinstance(call.data, dict) or call.data.get("guard_refused") is not True:
            raise RunStateError("custom collaboration respond guard did not refuse")
        return call

    def trial_messages(self) -> AdapterCall:
        if self.trial_session_id is None or self.trial_run_id is None:
            raise RunStateError("custom collaboration history read requires its Run")
        # The restored bcsPanel.StateMachineRunView history rides the
        # create-bound consultant Session that owns the one-shot Run (the
        # session-domain Run start never derives a second handle).
        call = self._cli(TRIAL_MESSAGES_OPERATION, case_class="trial-messages",
                         session_id=self.trial_session_id, run_id=self.trial_run_id)
        if not isinstance(call.data, list):
            raise RunStateError("custom collaboration restored history differs")
        return call

    def cancel_trial_run(self) -> AdapterCall:
        if self.trial_run_id is None:
            raise RunStateError("custom collaboration Run cancel requires its Run")
        call = self._http(RUN_CANCEL_OPERATION, case_class="cleanup-cancel",
                          body={"reason": "E2E one-shot trial completed"})
        data = call.data
        run = data.get("run") if isinstance(data, dict) else None
        if (not isinstance(run, dict) or run.get("run_id") != self.trial_run_id
                or run.get("status") not in ("aborted", "completed", "failed")
                or call.evidence.get("http_status") != 200):
            raise RunStateError("custom collaboration Run cancellation differs")
        return call

    def read_trial_session(self) -> AdapterCall:
        # Session preservation reads the create-bound consultant Session: on
        # remote tiers the station finalizes the run-scoped derived Session
        # completed/aborted at cancel while the create-bound Session preserves
        # running at read time (live r13 evidence).
        if self.trial_session_id is None or self.trial_group_id is None:
            raise RunStateError("custom collaboration Session read requires its identities")
        call = self._http(SESSION_READ_OPERATION, case_class="cleanup-session-read",
                          session_id=self.trial_session_id, group_id=self.trial_group_id)
        data = call.data
        if (not isinstance(data, dict)
                or (data.get("session_id") or data.get("id")) != self.trial_session_id
                or data.get("group_id") != self.trial_group_id
                or data.get("status") != "running"
                or call.evidence.get("http_status") != 200):
            raise RunStateError("custom collaboration trial Session preservation differs")
        return call

    def _group_cleanup(self, operation: str, *, case_class: str) -> AdapterCall:
        persistent = case_class.startswith("persistent")
        group_id = self.persistent_group_id if persistent else self.trial_group_id
        if not group_id:
            raise RunStateError(
                f"custom collaboration {case_class} requires its exact Group ID")
        if operation == GROUP_DELETE_OPERATION:
            call = self._http(operation, case_class=case_class, group_id=group_id)
            if (call.data != {"deleted": True, "id": group_id}
                    or call.evidence.get("http_status") != 200):
                raise RunStateError("custom collaboration Group deletion differs")
            return call
        call = self._http(operation, case_class=case_class, group_id=group_id,
                          expected=(404,), parse_error=True)
        data = call.data
        if (call.evidence.get("http_status") != 404 or not isinstance(data, dict)
                or (data.get("status") != 404 and data.get("code") != "group_not_found")):
            raise RunStateError(
                "custom collaboration Group deletion proof differs from the 404 contract")
        return call

    def delete_trial_group(self) -> AdapterCall:
        return self._group_cleanup(GROUP_DELETE_OPERATION, case_class="cleanup-delete")

    def verify_trial_group_deleted(self) -> AdapterCall:
        return self._group_cleanup(
            GROUP_VERIFY_DELETED_OPERATION, case_class="cleanup-verify-deleted")

    def create_persistent_group(self) -> AdapterCall:
        if self.persistent_group_id is not None:
            raise RunStateError("Custom collaboration persistent Group creation is one-shot")
        call = self._cli(CREATE_PERSISTENT_GROUP_OPERATION,
                         case_class="create-persistent-group",
                         yaml_path=self.runner.definition_path)
        data = call.data
        if (not isinstance(data, dict) or not data.get("id") or not data.get("session_id")
                or data.get("driver_bot") != self.driver_id
                or data.get("created") is not True
                or set(data.get("participants") or []) != {self.driver_id, self.member_id}):
            raise RunStateError("custom collaboration persistent Group differs")
        self.persistent_group_id = data["id"]
        return call

    def read_persistent_group(self) -> AdapterCall:
        if self.persistent_group_id is None:
            raise RunStateError("custom collaboration readback requires its Group ID")
        call = self._cli(READ_PERSISTENT_GROUP_OPERATION,
                         case_class="read-persistent-group",
                         group_id=self.persistent_group_id)
        data = call.data
        if (not isinstance(data, dict) or data.get("id") != self.persistent_group_id
                or data.get("driver_bot") != self.driver_id
                or data.get("group_strategy") != "state_machine"):
            raise RunStateError("custom collaboration Group readback differs")
        return call

    def delete_persistent_group(self) -> AdapterCall:
        return self._group_cleanup(GROUP_DELETE_OPERATION,
                                   case_class="persistent-cleanup-delete")

    def verify_persistent_group_deleted(self) -> AdapterCall:
        return self._group_cleanup(
            GROUP_VERIFY_DELETED_OPERATION, case_class="persistent-cleanup-verify-deleted")


class DualCustomCollaborationAdapter(RemoteLocalCustomCollaborationAdapter):
    """Dual-environment Custom-collaboration adapter identity."""


def _require_step_evidence(adapter: object, call: object, operation: str) -> dict:
    if not isinstance(call, AdapterCall):
        raise RunStateError(f"Custom collaboration {operation} did not return AdapterCall")
    call.validate()
    evidence = call.evidence
    if (evidence.get("transport") != expected_step_transport(operation)
            or not isinstance(evidence.get("caller_role"), str)
            or not evidence["caller_role"]
            or evidence.get("credential_kind")
            != custom_collaboration_step_credential_kind(adapter.profile, operation)
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False
            or evidence.get("fixture_effect") is None):
        raise RunStateError(f"Custom collaboration evidence for {operation} is invalid")
    if operation in SCENARIO_CLEANUP_OPERATIONS:
        expected_effect = (CUSTOM_COLLABORATION_CREATE_EFFECT if str(
            evidence.get("case_class", "")).startswith("persistent")
            else CUSTOM_COLLABORATION_TRIAL_EFFECT)
    else:
        expected_effect = _STEP_EFFECTS[operation]
    if evidence.get("fixture_effect") != expected_effect:
        raise RunStateError(f"Custom collaboration evidence for {operation} is invalid")
    return dict(evidence)


def execute_custom_collaboration_case(adapter: object) -> CaseExecution:
    """Run the three reviewed obligations in source order, then clean up."""
    custom_collaboration_cli_case_plan()
    if not isinstance(adapter, DualCustomCollaborationAdapter):
        raise RunStateError("Custom collaboration requires its dual adapter")
    records: dict[str, dict] = {}
    projections: dict[str, object] = {}
    error_type: str | None = None
    adapter.failed_method = None
    adapter.failure_class = None
    try:
        for operation, method_name in CUSTOM_COLLABORATION_CASE_METHODS:
            adapter.failed_method = method_name
            call = getattr(adapter, method_name)()
            records[method_name] = _require_step_evidence(adapter, call, operation)
            projections[method_name] = call.data
    except Exception as error:
        error_type = type(error).__name__
        adapter.failure_class = public_failure_class(error)

    def step(requirement_id: str, method: str, data: object,
             methods: tuple[str, ...], effect: str) -> StepOutcome:
        if error_type is not None:
            return StepOutcome(requirement_id=requirement_id, method=method,
                               status="NOT_RUN", data=None, evidence={},
                               error_type=error_type,
                               error_gap=adapter.failure_class)
        return StepOutcome(
            requirement_id=requirement_id, method=method, status="PASS", data=data,
            evidence={"requests": [dict(records[name]) for name in methods],
                      "fixture_effect": effect, "remote_validation": "NOT_RUN",
                      "grants_release_credit": False}, error_type=None)

    trial_data = create_data = None
    if error_type is None:
        trial_data = {
            "trial_group_id": adapter.trial_group_id,
            "trial_group_strategy": "state_machine",
            "trial_group_session_handle": adapter.trial_group_session_handle,
            "trial_session_id": adapter.trial_session_id,
            "trial_session_origin": "create_bound_consultant_session",
            "trial_run_id": adapter.trial_run_id,
            "run_start_transport": "product_cli_session_domain",
            "session_domain_leaves": ["trial_add_member", "trial_permission",
                                      "trial_start_run"],
            "pending_human_inputs": 0,
            "respond_guard": "REFUSED", "restored_history": True, "run_cancelled": True,
            "session_preserved_running": True, "group_cleanup": "DELETED",
            "group_delete_verified_404": True,
            "converged_transport": (
                "persistent-form trial Group via the reviewed create leaf; Human "
                "consultant membership, trial authority and the one-shot Run start "
                "ride the upstream-anchored session-domain product-CLI leaves; "
                "run-domain CLI reads, respond guard, cancel and exact delete follow")}
        create_data = {
            "group_id": adapter.persistent_group_id,
            "readback_group_strategy": "state_machine", "group_cleanup": "DELETED",
            "group_delete_verified_404": True}
    steps = (
        step(REQUIREMENTS[0], "validate_yaml", projections.get("validate_yaml"),
             ("validate_yaml",), CUSTOM_COLLABORATION_VALIDATE_EFFECT),
        step(REQUIREMENTS[2], "trial_one_shot_lifecycle", trial_data, (
            "trial_create_group", "trial_add_member", "trial_permission",
            "trial_start_run", "trial_query_graph",
            "trial_query_pending", "trial_respond_guard", "trial_messages",
            "cancel_trial_run", "read_trial_session",
            "delete_trial_group", "verify_trial_group_deleted"),
            CUSTOM_COLLABORATION_TRIAL_EFFECT),
        step(REQUIREMENTS[1], "create_persistent_group", create_data, (
            "create_persistent_group", "read_persistent_group",
            "delete_persistent_group", "verify_persistent_group_deleted"),
            CUSTOM_COLLABORATION_CREATE_EFFECT),
    )
    # Density is projected from actual leaf payloads through the same
    # event/bindings contract as the owned-local executor (S14 arbitration:
    # one projector, session-domain CLI transport, declared HTTP fallback).
    events: dict = {}
    bindings: dict = {"driver": adapter.driver_id, "reviewer": adapter.member_id,
                      "human": adapter.human_actor_id}
    base_statuses: dict = {}
    if error_type is None:
        validate_data = projections.get("validate_yaml") or {}
        events["validate"] = {key: validate_data.get(key) for key in ("valid", "summary")}
        events["definition"] = definition_evidence(DEFINITION_YAML)
        trial_create = projections.get("trial_create_group") or {}
        events["trial"] = {"session_id": trial_create.get("session_id")}
        membership = projections.get("trial_add_member") or {}
        events["member"] = {
            "session_id": membership.get("session_id") or membership.get("id"),
            "participants": membership.get("participants", [])}
        permission = projections.get("trial_permission") or {}
        events["permission"] = {key: permission.get(key)
                                for key in ("allowed", "session_id", "group_id")}
        started_run = (projections.get("trial_start_run") or {}).get("run") or {}
        events["run"] = {key: started_run.get(key) for key in ("run_id", "session_id", "group_id")}
        graph_run = (projections.get("trial_query_graph") or {}).get("run") or {}
        events["graph"] = {"run_id": graph_run.get("run_id")}
        events["pending"] = projections.get("trial_query_pending")
        guard = projections.get("trial_respond_guard") or {}
        events["guard"] = {"exit_code": guard.get("exit_code"),
                           "not_pending": guard.get("not_pending")}
        events["panel"] = panel_evidence(projections.get("trial_messages") or [],
                                         adapter.trial_run_id)
        events["cancel"] = projections.get("cancel_trial_run") or {}
        events["session"] = projections.get("read_trial_session") or {}
        events["trial_deleted"] = projections.get("delete_trial_group") or {}
        events["persistent"] = projections.get("create_persistent_group") or {}
        events["persistent_read"] = projections.get("read_persistent_group") or {}
        events["persistent_deleted"] = projections.get("delete_persistent_group") or {}
        bindings.update(trial_group=adapter.trial_group_id or "",
                        trial_session=adapter.trial_session_id or "",
                        run_id=adapter.trial_run_id or "",
                        persistent_group=adapter.persistent_group_id or "")
        base_statuses = {REQUIREMENTS[0]: "PASS", REQUIREMENTS[1]: "PASS",
                         REQUIREMENTS[2]: "PASS"}
    density_rows = project(events, bindings, base_statuses)["requirements"]
    operation_for_method = {method: operation
                            for operation, method in CUSTOM_COLLABORATION_CASE_METHODS}
    density_steps = []
    for row in density_rows:
        if row["id"] in (REQUIREMENTS[0], REQUIREMENTS[1], REQUIREMENTS[2]):
            continue
        method = row["method"]
        if error_type is not None:
            density_steps.append(StepOutcome(
                requirement_id=row["id"], method=method, status="NOT_RUN",
                data=None, evidence={}, error_type=error_type,
                error_gap=adapter.failure_class))
            continue
        if row["status"] == "PASS":
            operation = operation_for_method[method]
            if operation in SCENARIO_CLEANUP_OPERATIONS:
                # The shared Group delete/proof routes serve both cleanup
                # segments at once; a density row observes exactly one segment,
                # so its effect follows the observed segment - the same rule
                # _require_step_evidence applies to the shared routes.
                fixture_effect = (CUSTOM_COLLABORATION_CREATE_EFFECT
                                  if "persistent" in method
                                  else CUSTOM_COLLABORATION_TRIAL_EFFECT)
            else:
                fixture_effect = _STEP_EFFECTS[operation]
            density_steps.append(StepOutcome(
                requirement_id=row["id"], method=method, status="PASS", data=row,
                evidence={"requests": [records[method]],
                          "fixture_effect": fixture_effect,
                          "remote_validation": "NOT_RUN",
                          "grants_release_credit": False}, error_type=None))
        elif row["status"] == "FAIL":
            density_steps.append(StepOutcome(
                requirement_id=row["id"], method=method, status="FAIL",
                data=None, evidence={}, error_type="DensityAssertionFailed",
                error_gap=None))
        else:
            density_steps.append(StepOutcome(
                requirement_id=row["id"], method=method, status="NOT_RUN",
                data=None, evidence={}, error_type="DensityEvidenceGap",
                error_gap="SOURCE_FIELD_NOT_OBSERVED"))
    steps += tuple(density_steps)
    completed = sum(row.status == "PASS" for row in steps)
    complete = completed == len(REQUIREMENTS)
    execution = CaseExecution(
        case_id=DUAL_CUSTOM_COLLAB_CASE_ID, parent_id=PARENT,
        status="PASS" if complete else "BLOCKED", steps=steps,
        completed_count=completed, total_count=len(REQUIREMENTS),
        error_type=error_type or (None if complete else "DensityEvidenceGap"),
        ready_to_execute=complete)
    execution.validate()
    return execution


_CASES = {
    "custom-collaboration": {
        "case_id": DUAL_CUSTOM_COLLAB_CASE_ID,
        "validator": validate_custom_collaboration_cli_profile,
        "adapter": DualCustomCollaborationAdapter,
        "execute": execute_custom_collaboration_case,
        "fixture_effect": CUSTOM_COLLABORATION_CLI_EFFECT,
    },
}


def load_custom_collaboration_case_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("custom collaboration profile must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("custom collaboration profile is unreadable JSON") from None
    return validate_custom_collaboration_cli_profile(raw)


def dual_custom_collaboration_case_run(
    *,
    profile_path: Path,
    cli_binary: Path,
    cli_binary_sha256: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    run_id = identifier(run_id, "custom collaboration run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "custom collaboration requires explicit write permission and exact Run confirmation")
    profile = load_custom_collaboration_case_profile(profile_path)
    require_resolved(profile)
    digest = fingerprint(profile)
    artifact = executor_digest()
    requested_state_dir = Path(state_dir)
    if (not requested_state_dir.is_absolute() or requested_state_dir.is_symlink()
            or requested_state_dir.exists()
            or not requested_state_dir.parent.is_dir()
            or requested_state_dir.parent.is_symlink()):
        raise ConfigError("custom collaboration requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("custom collaboration requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)
    try:
        definition = state_dir / DEFINITION_FILE
        definition.write_text(DEFINITION_YAML, encoding="utf-8")
        if (definition.is_symlink() or not definition.is_file()
                or definition.stat().st_size > 1_048_576):
            raise RunStateError("custom collaboration definition YAML is not a bounded file")
        write(state_dir / PROFILE_FILE, profile)
        binding = {
            "schema_version": SCHEMA_VERSION, "contract": REPORT_CONTRACT,
            "case_id": DUAL_CUSTOM_COLLAB_CASE_ID, "parent_id": PARENT,
            "run_id": run_id, "profile_digest": digest, "executor_digest": artifact,
            "cli_binary_sha256": cli_binary_sha256, "state_dir": str(state_dir)}
        write(state_dir / BINDING_FILE, binding)
        runner = CustomCollaborationCliRunner(
            profile, binary=Path(cli_binary).resolve(),
            binary_sha256=cli_binary_sha256, state_dir=state_dir)
        adapter = DualCustomCollaborationAdapter(profile, runner)
        execution = execute_custom_collaboration_case(adapter)
    except (ConfigError, RunStateError, WorkspaceCliRunnerError,
            WriteTransportError, OSError) as error:
        raise ConfigError(
            f"custom collaboration case execution is unavailable: {error}") from None
    report = {
        "schema_version": SCHEMA_VERSION, "contract": REPORT_CONTRACT,
        "case_id": execution.case_id, "parent_id": PARENT, "run_id": run_id,
        "status": "PASS" if execution.status == "PASS" else "BLOCKED",
        "profile_digest": digest, "executor_digest": artifact,
        "cli_binary_sha256": cli_binary_sha256, "state_dir": str(state_dir),
        "environment": {"tier": profile["environment"]["tier"],
                        "id": profile["environment"]["id"],
                        "entrypoint_kind": profile["entrypoint"]["kind"]},
        "case": execution.snapshot(),
        "execution": {"status": execution.status,
                      "completed_count": execution.completed_count,
                      "total_count": execution.total_count,
                      "error_type": execution.error_type},
        "failed_method": (adapter.failed_method
                          if execution.error_type is not None else None),
        "failure_class": (adapter.failure_class
                          if execution.error_type is not None else None),
        "resource_disposition": CUSTOM_COLLABORATION_CLI_EFFECT,
        "remote_validation": "NOT_RUN", "grants_release_credit": False,
        "credential_kind": custom_collaboration_cli_credential_kind(profile)}
    write(state_dir / REPORT_FILE, report)
    return report
