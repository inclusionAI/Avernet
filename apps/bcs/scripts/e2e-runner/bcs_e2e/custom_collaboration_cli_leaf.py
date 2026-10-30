"""Frozen vocabulary and the runner plant for the dual Custom-collaboration case.

The reviewed obligation map, the product-CLI leaf vocabulary, the cleanup
WriteClient exit routes, the per-operation effect segmentation, the
per-operation credential labels, the exact leaf argument templates and the
one-shot launcher envelope live here so the case-run module stays under the
source-file budget.  The runner executes the frozen product ``bcs-cli``
leaves through the fixed simulator-wrapper envelope; it never journals a
credential.
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import re
import shutil
import time
import uuid

from .bounded_process import ProcessCaptureError, capture
from .local_cli import (
    CUSTOM_COLLABORATION_CONTEXT,
    CUSTOM_COLLABORATION_TOPIC,
    LocalCliError,
    TRIAL_CONTEXT,
    TRIAL_INPUT,
    TRIAL_TOPIC,
    require_cli_binary,
)
from .local_custom_collaboration_run import DEFINITION_PATH, DEFINITION_YAML
from .local_custom_collaboration_case import REQUIREMENTS, DENSITY_SPECS
from .profile import (
    ConfigError,
    identifier,
    object_keys,
    origin,
    parsed_origin,
    unique_keys,
)
from .run_state import write
from .workspace_cli_commands import WorkspaceCliRunnerError

CUSTOM_COLLABORATION_CLI_EFFECT = (
    "OWNED_CUSTOM_COLLABORATION_PARENT_LIFECYCLE_THEN_EXACT_DELETE")
CUSTOM_COLLABORATION_VALIDATE_EFFECT = (
    "CUSTOM_COLLABORATION_YAML_VALIDATION_READ_ONLY")
CUSTOM_COLLABORATION_CREATE_EFFECT = (
    "OWNED_CUSTOM_COLLABORATION_PERSISTENT_GROUP_THEN_EXACT_DELETE")
CUSTOM_COLLABORATION_TRIAL_EFFECT = (
    "OWNED_CUSTOM_COLLABORATION_ONE_SHOT_TRIAL_SESSION_PRESERVED_THEN_EXACT_DELETE")

# Product-CLI leaves (PRODUCT_CLI), in the exact execution order of the
# frozen owned-local parent: validate, the one-shot trial lifecycle, then
# the persistent Group create with readback.
VALIDATE_YAML_OPERATION = "custom_collaboration_cli.validate"
CREATE_PERSISTENT_GROUP_OPERATION = "custom_collaboration_cli.create_persistent_group"
READ_PERSISTENT_GROUP_OPERATION = "custom_collaboration_cli.read_persistent_group"
TRIAL_CREATE_GROUP_OPERATION = "custom_collaboration_cli.trial_create_group"
TRIAL_QUERY_GRAPH_OPERATION = "custom_collaboration_cli.trial_query_graph"
TRIAL_QUERY_PENDING_OPERATION = "custom_collaboration_cli.trial_query_pending"
TRIAL_RESPOND_GUARD_OPERATION = "custom_collaboration_cli.trial_respond_guard"
TRIAL_MESSAGES_OPERATION = "custom_collaboration_cli.trial_messages"
TRIAL_ADD_MEMBER_OPERATION = "custom_collaboration_cli.trial_add_member"
TRIAL_PERMISSION_OPERATION = "custom_collaboration_cli.trial_permission"
TRIAL_START_RUN_OPERATION = "custom_collaboration_cli.trial_start_run"

# Cleanup WriteClient exits (PRODUCT_HTTP) on the reviewed legacy routes.
RUN_CANCEL_OPERATION = "custom_collaboration_cli.run.cancel"
SESSION_READ_OPERATION = "custom_collaboration_cli.session.read"
GROUP_DELETE_OPERATION = "custom_collaboration_cli.group.delete"
GROUP_VERIFY_DELETED_OPERATION = "custom_collaboration_cli.group.verify_deleted"
# Declared fallback exit only (S14 arbitration, 2026-10-30): the reviewed
# one-shot Run transport is the session-domain product-CLI leaf
# "collaborate run"; this group-domain HTTP entry stays in the frozen
# route_bindings/profile contract shape but is never executed by the case.
GROUP_RUN_START_OPERATION = "custom_collaboration_cli.run.start_group_domain"

CUSTOM_COLLABORATION_CLI_OPERATIONS = (
    VALIDATE_YAML_OPERATION, TRIAL_CREATE_GROUP_OPERATION, TRIAL_ADD_MEMBER_OPERATION,
    TRIAL_PERMISSION_OPERATION, TRIAL_START_RUN_OPERATION, TRIAL_QUERY_GRAPH_OPERATION,
    TRIAL_QUERY_PENDING_OPERATION, TRIAL_RESPOND_GUARD_OPERATION,
    TRIAL_MESSAGES_OPERATION, CREATE_PERSISTENT_GROUP_OPERATION,
    READ_PERSISTENT_GROUP_OPERATION,
)
_HTTP_PRODUCT_OPERATIONS = frozenset({
    RUN_CANCEL_OPERATION, SESSION_READ_OPERATION,
    GROUP_DELETE_OPERATION, GROUP_VERIFY_DELETED_OPERATION,
})
# Scenario (non-cleanup) HTTP exits: none execute. The group-domain Run
# start is a declared fallback entry (route_bindings/profile shape only);
# the reviewed one-shot Run transport is the session-domain product-CLI
# leaf (S14 arbitration 2026-10-30).
_HTTP_SCENARIO_OPERATIONS = frozenset()
# The Group delete and delete-proof routes serve both cleanup segments;
# their fixture_effect is labelled per segment, never per route name.
SCENARIO_CLEANUP_OPERATIONS = frozenset({GROUP_DELETE_OPERATION,
                                         GROUP_VERIFY_DELETED_OPERATION})

CUSTOM_COLLABORATION_REQUIREMENT_OPERATIONS = {
    "CUSTOM-COLLABORATION.validate-yaml": (VALIDATE_YAML_OPERATION,),
    "CUSTOM-COLLABORATION.create-persistent-group": (
        CREATE_PERSISTENT_GROUP_OPERATION, READ_PERSISTENT_GROUP_OPERATION,
        GROUP_DELETE_OPERATION, GROUP_VERIFY_DELETED_OPERATION),
    "CUSTOM-COLLABORATION.trial-one-shot-lifecycle": (
        TRIAL_CREATE_GROUP_OPERATION, TRIAL_ADD_MEMBER_OPERATION,
        TRIAL_PERMISSION_OPERATION, TRIAL_START_RUN_OPERATION,
        TRIAL_QUERY_GRAPH_OPERATION, TRIAL_QUERY_PENDING_OPERATION,
        TRIAL_RESPOND_GUARD_OPERATION, TRIAL_MESSAGES_OPERATION,
        RUN_CANCEL_OPERATION, SESSION_READ_OPERATION,
        GROUP_DELETE_OPERATION, GROUP_VERIFY_DELETED_OPERATION),
}
CUSTOM_COLLABORATION_CASE_METHODS = (
    (VALIDATE_YAML_OPERATION, "validate_yaml"),
    (TRIAL_CREATE_GROUP_OPERATION, "trial_create_group"),
    (TRIAL_ADD_MEMBER_OPERATION, "trial_add_member"),
    (TRIAL_PERMISSION_OPERATION, "trial_permission"),
    (TRIAL_START_RUN_OPERATION, "trial_start_run"),
    (TRIAL_QUERY_GRAPH_OPERATION, "trial_query_graph"),
    (TRIAL_QUERY_PENDING_OPERATION, "trial_query_pending"),
    (TRIAL_RESPOND_GUARD_OPERATION, "trial_respond_guard"),
    (TRIAL_MESSAGES_OPERATION, "trial_messages"),
    (RUN_CANCEL_OPERATION, "cancel_trial_run"),
    (SESSION_READ_OPERATION, "read_trial_session"),
    (GROUP_DELETE_OPERATION, "delete_trial_group"),
    (GROUP_VERIFY_DELETED_OPERATION, "verify_trial_group_deleted"),
    (CREATE_PERSISTENT_GROUP_OPERATION, "create_persistent_group"),
    (READ_PERSISTENT_GROUP_OPERATION, "read_persistent_group"),
    (GROUP_DELETE_OPERATION, "delete_persistent_group"),
    (GROUP_VERIFY_DELETED_OPERATION, "verify_persistent_group_deleted"),
)
# Empty operation tuples denote intentionally unchained session-domain leaves.
CUSTOM_COLLABORATION_REQUIREMENT_OPERATIONS.update({
    "CUSTOM-COLLABORATION." + name: tuple(op for op, candidate in CUSTOM_COLLABORATION_CASE_METHODS
                                         if candidate == method)
    for name, method, _, _ in DENSITY_SPECS
})
_STEP_EFFECTS = {
    VALIDATE_YAML_OPERATION: CUSTOM_COLLABORATION_VALIDATE_EFFECT,
    CREATE_PERSISTENT_GROUP_OPERATION: CUSTOM_COLLABORATION_CREATE_EFFECT,
    READ_PERSISTENT_GROUP_OPERATION: CUSTOM_COLLABORATION_CREATE_EFFECT,
    TRIAL_CREATE_GROUP_OPERATION: CUSTOM_COLLABORATION_TRIAL_EFFECT,
    TRIAL_QUERY_GRAPH_OPERATION: CUSTOM_COLLABORATION_TRIAL_EFFECT,
    TRIAL_QUERY_PENDING_OPERATION: CUSTOM_COLLABORATION_TRIAL_EFFECT,
    TRIAL_RESPOND_GUARD_OPERATION: CUSTOM_COLLABORATION_TRIAL_EFFECT,
    TRIAL_MESSAGES_OPERATION: CUSTOM_COLLABORATION_TRIAL_EFFECT,
    RUN_CANCEL_OPERATION: CUSTOM_COLLABORATION_TRIAL_EFFECT,
    SESSION_READ_OPERATION: CUSTOM_COLLABORATION_TRIAL_EFFECT,
    TRIAL_ADD_MEMBER_OPERATION: CUSTOM_COLLABORATION_TRIAL_EFFECT,
    TRIAL_PERMISSION_OPERATION: CUSTOM_COLLABORATION_TRIAL_EFFECT,
    TRIAL_START_RUN_OPERATION: CUSTOM_COLLABORATION_TRIAL_EFFECT,
}
CUSTOM_COLLABORATION_CLI_RELATIONSHIPS = {
    "yaml_validation": "read-only-no-durable-resource",
    "persistent_group": "owned-create-readback-then-exact-delete",
    "trial_lifecycle": "owned-one-shot-run-cancelled-session-running",
    "trial_membership": "case-owned-human-consultant",
    "run_lifecycle": "case-owned-one-shot-state-machine-run",
    "cleanup": "exact-owned-deletes-verified-404",
}
# Declared legacy cleanup routes (contract form with the Profile
# placeholder vocabulary).  The WriteClient templates understand only
# {group_id}, {session_id} and the actor placeholders, so the frozen
# run.cancel declaration keeps its {run_id} contract shape while the
# adapter derives the literal quoted path per Run.
DECLARED_CLEANUP_ROUTES = {
    RUN_CANCEL_OPERATION: ("POST", "/state-machine-runs/{run_id}/cancel"),
    SESSION_READ_OPERATION: ("GET", "/sessions/{session_id}"),
    GROUP_DELETE_OPERATION: ("DELETE", "/groups/{group_id}?bot_id={driver_actor_id}"),
    GROUP_VERIFY_DELETED_OPERATION: (
        "GET", "/groups/{group_id}?bot_id={driver_actor_id}"),
}
# Converged scenario exits (live-verified 2026-10-29, diag4c probe-b/b3): the
# group-domain Run start shares the reviewed cleanup binding vocabulary.
DECLARED_SCENARIO_ROUTES = {
    GROUP_RUN_START_OPERATION: (
        "POST", "/groups/{group_id}/state-machine-runs"),
}
DECLARED_CLI_EXIT_ROUTES = {**DECLARED_CLEANUP_ROUTES, **DECLARED_SCENARIO_ROUTES}
# (caller_role, argv head, admitted kwargs) per reviewed CLI leaf.
LEAF_ARGUMENTS = {
    VALIDATE_YAML_OPERATION: (
        "operator", ("collaboration", "validate"), ("yaml_path",)),
    CREATE_PERSISTENT_GROUP_OPERATION: (
        "driver", ("collaboration", "create"), ("yaml_path",)),
    READ_PERSISTENT_GROUP_OPERATION: (
        "driver", ("get-group", "--id"), ("group_id",)),
    TRIAL_CREATE_GROUP_OPERATION: (
        "driver", ("collaboration", "create"), ("yaml_path",)),
    TRIAL_QUERY_GRAPH_OPERATION: (
        "driver", ("collaborate", "query"), ("run_id",)),
    TRIAL_QUERY_PENDING_OPERATION: (
        "driver", ("collaborate", "query"), ("run_id",)),
    TRIAL_RESPOND_GUARD_OPERATION: (
        "driver", ("collaborate", "respond"), ("run_id",)),
    TRIAL_MESSAGES_OPERATION: (
        "driver", ("session", "messages"), ("session_id", "run_id")),
    TRIAL_ADD_MEMBER_OPERATION: (
        "driver", ("session", "add-member"), ("session_id", "human_id")),
    TRIAL_PERMISSION_OPERATION: (
        "driver", ("collaborate", "permission"), ("session_id",)),
    TRIAL_START_RUN_OPERATION: (
        "driver", ("collaborate", "run"), ("yaml_path", "session_id")),
}
# Static tail after the leaf head; tokens are substituted from the case
# identities exactly once, mirroring the owned-local parent argv.
LEAF_ARGUMENT_TEMPLATES = {
    VALIDATE_YAML_OPERATION: ("{yaml_path}",),
    CREATE_PERSISTENT_GROUP_OPERATION: (
        "{yaml_path}", "--driver", "{driver}", "--binding", "planner={driver}",
        "--binding", "reviewer={member}", "--context", CUSTOM_COLLABORATION_CONTEXT,
        "--topic", CUSTOM_COLLABORATION_TOPIC),
    READ_PERSISTENT_GROUP_OPERATION: ("{group_id}",),
    # Converged trial transport: the one-shot trial Group is created through
    # the same reviewed persistent-create form (bindings stored on the Group,
    # group_strategy pinned state_machine by the server), then Started via
    # the group-domain HTTP leaf below. TRIAL_CONTEXT/TRIAL_TOPIC stay
    # distinct from the persistent obligation so live fixtures remain
    # attributable per obligation.
    TRIAL_CREATE_GROUP_OPERATION: (
        "{yaml_path}", "--driver", "{driver}", "--binding", "planner={driver}",
        "--binding", "reviewer={member}", "--context", TRIAL_CONTEXT,
        "--topic", TRIAL_TOPIC),
    TRIAL_QUERY_GRAPH_OPERATION: ("--run", "{run_id}", "--graph"),
    TRIAL_QUERY_PENDING_OPERATION: ("--run", "{run_id}", "--pending"),
    TRIAL_RESPOND_GUARD_OPERATION: (
        "--run", "{run_id}", "--node", "plan", "--content", "invalid Human reply"),
    TRIAL_MESSAGES_OPERATION: (
        "{session_id}", "--view-bot", "{driver}", "--limit", "50"),
    # Upstream-anchored session-domain trial leaves (cli-stories.sh
    # 180-182, 194-196, 206-209): membership, authority and the one-shot
    # Run start ride the frozen product CLI exactly as the owned-local
    # parent does; the Human participant keeps the upstream identifier form.
    TRIAL_ADD_MEMBER_OPERATION: (
        "{session_id}", "--bot-uuid", "{human}", "--role", "consultant"),
    TRIAL_PERMISSION_OPERATION: ("--session", "{session_id}"),
    TRIAL_START_RUN_OPERATION: (
        "{yaml_path}", "--session", "{session_id}", "--binding", "planner={driver}",
        "--binding", "reviewer={member}", "--input", TRIAL_INPUT),
}


def custom_collaboration_cli_credential_kind(profile: dict) -> str:
    """Label the tier-aware credential family without echoing any secret."""
    if profile["environment"]["tier"] == "local":
        return "owned_local_bot_token"
    if profile["auth"]["kind"] == "human-cookie-with-owned-driver-bot-token":
        return "human_cookie_with_owned_driver_bot_token"
    return "human_cookie"


def expected_step_transport(operation: str) -> str:
    return ("PRODUCT_HTTP" if operation in _HTTP_PRODUCT_OPERATIONS
            or operation in _HTTP_SCENARIO_OPERATIONS
            else "PRODUCT_CLI")


def custom_collaboration_step_credential_kind(profile: dict,
                                              operation: str) -> str:
    """Per-operation credential label; the Singlebox validation preflight is
    anonymous, the remote one presents the family credential."""
    if operation == VALIDATE_YAML_OPERATION:
        if profile["environment"]["tier"] == "local":
            return "none"
        return custom_collaboration_cli_credential_kind(profile)
    if operation in _HTTP_PRODUCT_OPERATIONS or operation in _HTTP_SCENARIO_OPERATIONS:
        return ("owned_local_bot_token"
                if profile["environment"]["tier"] == "local" else "human_cookie")
    return custom_collaboration_cli_credential_kind(profile)


def participant_ids(participants: object) -> list:
    """Project the bot_uuid or bare-string forms of one participant list."""
    ids = []
    if isinstance(participants, list):
        for item in participants:
            if isinstance(item, dict):
                ids.append(item.get("bot_uuid"))
            elif isinstance(item, str):
                ids.append(item)
    return ids


class CustomCollaborationCliRunnerError(WorkspaceCliRunnerError):
    pass


_TOKEN_PATTERN = re.compile(
    r"\{(driver|member|human|yaml_path|group_id|session_id|run_id)\}")


def _render(part: str, values: dict) -> str:
    """Substitute reviewed identity tokens; literal JSON braces survive."""
    return _TOKEN_PATTERN.sub(lambda match: str(values[match.group(1)]), part)


def _json_object(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
    except (UnicodeError, ValueError):
        raise CustomCollaborationCliRunnerError(
            f"custom collaboration {label} did not return one JSON object") from None
    if not isinstance(value, dict):
        raise CustomCollaborationCliRunnerError(
            f"custom collaboration {label} did not return one JSON object")
    return dict(value)


def _failure_detail(stdout: bytes, stderr: bytes, secrets: list[bytes]) -> str:
    """Bounded honest nonzero-exit detail; credential bytes are scrubbed."""
    text = (stdout + b"\n" + stderr).decode("utf-8", "replace")
    for secret in secrets:
        if secret:
            text = text.replace(secret.decode("utf-8", "replace"), "[REDACTED]")
    return text.strip()[:500]


def _json_array(raw: bytes, label: str) -> list:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
    except (UnicodeError, ValueError):
        raise CustomCollaborationCliRunnerError(
            f"custom collaboration {label} did not return one JSON array") from None
    if not isinstance(value, list):
        raise CustomCollaborationCliRunnerError(
            f"custom collaboration {label} did not return one JSON array")
    return list(value)


CUSTOM_COLLABORATION_CLI_PROFILE_CONTRACT = "dual-custom-collaboration-cli-profile-v1"
_PROFILE_BASE_KEYS = frozenset({"schema_version", "contract", "environment",
    "entrypoint", "human", "actors", "auth", "relationships", "policy", "cli",
    "route_bindings"})
PREPROD_PROFILE_KEYS = _PROFILE_BASE_KEYS | {"browser_origin"}
LOCAL_PROFILE_KEYS = _PROFILE_BASE_KEYS


def _env_ref(value: object, where: str) -> str:
    if (not isinstance(value, str) or not 3 <= len(value) <= 100
            or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in value)):
        raise ConfigError(f"{where}: expected an uppercase environment reference")
    return value


def _actor(value: object, role: str, human: str) -> dict:
    actor = object_keys(value, {"bot_id", "owner_id", "dedicated", "visibility"},
                        f"actors.{role}")
    identifier(actor["bot_id"], f"actors.{role}.bot_id")
    if actor["owner_id"] != human or actor["dedicated"] is not True:
        raise ConfigError(f"actors.{role}: must be dedicated and owned by the Human")
    if actor["visibility"] != "public":
        raise ConfigError(f"actors.{role}.visibility: requires a public dedicated Bot")
    return actor


def _validate_cleanup_bindings(value: object) -> dict:
    if (not isinstance(value, dict)
            or set(value) != set(DECLARED_CLI_EXIT_ROUTES)):
        raise ConfigError(
            "route_bindings: custom collaboration requires the four reviewed "
            "cleanup exits and the converged group-domain Run start")
    result = {}
    for operation, (method, path) in DECLARED_CLI_EXIT_ROUTES.items():
        binding = object_keys(value[operation], {"method", "path", "api_face",
                                                "auth", "response", "traceparent"},
                              f"route_bindings.{operation}")
        if (binding["method"] != method or binding["path"] != path
                or binding["api_face"] != "legacy"
                or binding["auth"] != "case_owned_driver"
                or binding["response"] != "legacy-json"
                or binding["traceparent"] != "disabled"):
            raise ConfigError(
                f"route_bindings.{operation}: exit route differs from the product contract")
        result[operation] = dict(binding)
    return result


def validate_custom_collaboration_cli_profile(raw: object) -> dict:
    """Validate one dual-environment Custom-collaboration product-CLI Profile."""
    if not isinstance(raw, dict) and not hasattr(raw, "get"):
        raise ConfigError("custom collaboration CLI profile must be an object")
    preview = raw.get("environment") if isinstance(raw, dict) else None
    tier = preview.get("tier") if isinstance(preview, dict) else None
    if tier not in {"local", "preprod"}:
        raise ConfigError("environment.tier: only Singlebox and preprod are admitted")
    expected = PREPROD_PROFILE_KEYS if tier == "preprod" else LOCAL_PROFILE_KEYS
    profile = object_keys(raw, expected, "custom collaboration CLI profile")
    if (type(profile["schema_version"]) is not int or profile["schema_version"] != 1
            or profile["contract"] != CUSTOM_COLLABORATION_CLI_PROFILE_CONTRACT):
        raise ConfigError("custom collaboration CLI profile: unsupported version or contract")
    environment = object_keys(profile["environment"], {"tier", "id", "expected_bcs_env"},
                              "custom collaboration CLI environment")
    identifier(environment["id"], "environment.id")
    if environment["expected_bcs_env"] != environment["tier"]:
        if environment["tier"] != "preprod" or environment["expected_bcs_env"] != "pre":
            raise ConfigError("environment.expected_bcs_env: environment binding mismatch")
    entrypoint = object_keys(profile["entrypoint"],
                             {"kind", "origin", "api_face", "manifest_face"},
                             "custom collaboration CLI entrypoint")
    if entrypoint["api_face"] != "legacy" or entrypoint["manifest_face"] != "legacy":
        raise ConfigError("entrypoint: custom collaboration CLI requires the legacy face")
    target = parsed_origin(entrypoint["origin"], environment["tier"])
    if environment["tier"] == "local":
        if (entrypoint["kind"] != "singlebox" or target.scheme != "http"
                or target.hostname not in {"127.0.0.1", "localhost", "::1"}
                or not target.port):
            raise ConfigError(
                "entrypoint: local custom collaboration CLI requires an owned loopback")
    elif (entrypoint["kind"] != "gateway" or target.scheme != "https"
          or target.hostname != "bcs-pre.alipay.com"):
        raise ConfigError(
            "entrypoint: preprod custom collaboration CLI requires the admitted "
            "HTTPS gateway bcs-pre.alipay.com")
    human = object_keys(profile["human"], {"user_id"}, "human")
    identifier(human["user_id"], "human.user_id")
    actors = object_keys(profile["actors"], {"driver", "member"}, "actors")
    for role in ("driver", "member"):
        actors[role] = _actor(actors[role], role, human["user_id"])
    if actors["driver"]["bot_id"] == actors["member"]["bot_id"]:
        raise ConfigError("actors: custom collaboration requires two distinct dedicated Bots")
    if environment["tier"] == "local":
        auth = object_keys(profile["auth"], {"kind", "driver_token_env",
                                             "member_token_env"}, "auth")
        if auth["kind"] != "owned-local-bot-tokens":
            raise ConfigError(
                "auth.kind: local custom collaboration CLI requires owned Bot tokens")
        _env_ref(auth["driver_token_env"], "auth.driver_token_env")
        _env_ref(auth["member_token_env"], "auth.member_token_env")
    else:
        auth = profile["auth"]
        if (isinstance(auth, dict) and auth.get("kind") == "human-cookie"
                and set(auth) == {"kind", "cookie_env"}):
            _env_ref(auth["cookie_env"], "auth.cookie_env")
        elif (isinstance(auth, dict)
              and auth.get("kind") == "human-cookie-with-owned-driver-bot-token"
              and set(auth) == {"kind", "driver_token_env", "cookie_env"}):
            _env_ref(auth["driver_token_env"], "auth.driver_token_env")
            _env_ref(auth["cookie_env"], "auth.cookie_env")
        else:
            raise ConfigError(
                "auth.kind: preprod custom collaboration CLI requires the Human Cookie "
                "or the reviewed bundle (Human Cookie plus the owned driver Bot token)")
        origin(profile["browser_origin"], "preprod")
        if parsed_origin(profile["browser_origin"], "preprod").hostname != "teamclaw-pre.alipay.com":
            raise ConfigError(
                "browser_origin: preprod custom collaboration pins teamclaw-pre.alipay.com")
    relationships = object_keys(profile["relationships"],
                                set(CUSTOM_COLLABORATION_CLI_RELATIONSHIPS), "relationships")
    if relationships != CUSTOM_COLLABORATION_CLI_RELATIONSHIPS:
        raise ConfigError(
            "relationships: retained custom-collaboration effect admission differs")
    policy = object_keys(profile["policy"], {"fixture_pool", "exclusive_use_confirmed",
                                             "timeout_seconds", "max_response_bytes",
                                             "failure_retention_hours"}, "policy")
    identifier(policy["fixture_pool"], "policy.fixture_pool")
    if type(policy["exclusive_use_confirmed"]) is not bool or not policy["exclusive_use_confirmed"]:
        raise ConfigError(
            "policy.exclusive_use_confirmed: custom-collaboration effects require exclusive use")
    for key, maximum in (("timeout_seconds", 30), ("max_response_bytes", 65536),
                         ("failure_retention_hours", 168)):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ConfigError(f"policy.{key}: integer outside the CLI contract")
    cli = object_keys(profile["cli"], {"transport", "command_family", "api_face"}, "cli")
    if cli != {"transport": "product-cli", "command_family": "collaboration",
               "api_face": "legacy"}:
        raise ConfigError("cli: product CLI binding differs from the case contract")
    profile["route_bindings"] = _validate_cleanup_bindings(profile["route_bindings"])
    return json.loads(json.dumps(profile, allow_nan=False), object_pairs_hook=unique_keys)



class CustomCollaborationCliRunner:
    """Execute the eleven reviewed product-CLI leaves once, in order.

    The launcher envelope mirrors the fixed simulator-wrapper form: a
    private ``~/.bcs-e2e/runner-<hex>`` root widened for the simulator
    uid, a docker directory on the child PATH, ``DOCKER_HOST`` forwarding,
    and a CLEARED private-context cleanup that never journals credentials.
    """

    _LAUNCHER_ROOT_BASE = Path.home() / ".bcs-e2e"
    _TOKEN_KINDS = frozenset({"owned_local_bot_token", "owned_bot_token",
                              "human_cookie_with_owned_driver_bot_token",
                              "human_cookie_with_owned_bot_tokens"})
    _COOKIE_KINDS = frozenset({"human_cookie_with_owned_driver_bot_token",
                               "human_cookie_with_owned_bot_tokens"})

    def __init__(self, profile: dict, *, binary: Path, binary_sha256: str,
                 state_dir: Path):
        self.profile = validate_custom_collaboration_cli_profile(profile)
        if (not isinstance(state_dir, Path) or not state_dir.is_absolute()
                or state_dir.is_symlink() or not state_dir.is_dir()):
            raise CustomCollaborationCliRunnerError(
                "custom collaboration CLI requires an absolute current Run directory")
        try:
            require_cli_binary(binary, binary_sha256)
        except LocalCliError:
            raise CustomCollaborationCliRunnerError(
                "custom collaboration CLI binary is not approved") from None
        self.binary = Path(binary).resolve()
        self.binary_sha256 = binary_sha256
        self.state_dir = state_dir
        self.origin = self.profile["entrypoint"]["origin"]
        self.tier = self.profile["environment"]["tier"]
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.human_id = self.profile["human"]["user_id"]
        self.definition_path = state_dir / DEFINITION_PATH
        self.executed: list[str] = []
        self._sweep_stale_launcher_roots()

    def _credentials(self) -> tuple[str, str, str]:
        """Return (credential_kind, driver token, cookie); never a secret."""
        if self.tier == "local":
            value = os.environ.get(self.profile["auth"]["driver_token_env"], "")
            if not value:
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration CLI owned driver Bot credential is absent")
            return "owned_local_bot_token", value, ""
        auth = self.profile["auth"]
        if auth["kind"] == "human-cookie-with-owned-driver-bot-token":
            value = os.environ.get(auth["driver_token_env"], "")
            cookie = os.environ.get(auth["cookie_env"], "")
            if not value or not cookie:
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration CLI bundle (owned driver Bot token plus "
                    "the Human Cookie) is absent")
            return "human_cookie_with_owned_driver_bot_token", value, cookie
        cookie = os.environ.get(auth["cookie_env"], "")
        if not cookie:
            raise CustomCollaborationCliRunnerError(
                "custom collaboration CLI Human Cookie is absent")
        return "human_cookie", "", cookie

    def _contract(self, operation: str, **kwargs) -> tuple[str, tuple[str, ...]]:
        if set(kwargs) != set(LEAF_ARGUMENTS[operation][2]):
            raise CustomCollaborationCliRunnerError(
                "custom collaboration CLI leaf arguments differ from the case plan")
        values = {"driver": self.driver_id, "member": self.member_id,
                  "human": "human_" + self.human_id}
        if "yaml_path" in kwargs:
            self._require_definition()
            if not isinstance(kwargs["yaml_path"], Path):
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration CLI leaf requires its owned YAML path")
            values["yaml_path"] = str(kwargs["yaml_path"])
        if "group_id" in kwargs:
            values["group_id"] = identifier(
                kwargs["group_id"], "custom collaboration persistent Group ID")
        if "session_id" in kwargs:
            values["session_id"] = identifier(
                kwargs["session_id"], "custom collaboration trial Session ID")
        if "run_id" in kwargs:
            values["run_id"] = identifier(
                kwargs["run_id"], "custom collaboration Run ID")
        role, head = LEAF_ARGUMENTS[operation][0], LEAF_ARGUMENTS[operation][1]
        arguments = tuple(_render(part, values)
                          for part in LEAF_ARGUMENT_TEMPLATES[operation])
        return role, (*head, *arguments)

    def _require_definition(self) -> None:
        path = self.definition_path
        if path.is_symlink() or not path.is_file():
            raise CustomCollaborationCliRunnerError(
                "custom collaboration definition YAML is missing from the Run state")
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise CustomCollaborationCliRunnerError(
                "custom collaboration definition YAML is unreadable") from None
        if path.stat().st_size > 1_048_576 or raw != DEFINITION_YAML:
            raise CustomCollaborationCliRunnerError(
                "custom collaboration definition YAML differs from the frozen source")

    def _context(self, credential_kind: str, token: str, cookie: str):
        root = self._LAUNCHER_ROOT_BASE / f"runner-{uuid.uuid4().hex}"
        root.mkdir(mode=0o700, parents=True)
        for name in ("home", "bot", "tmp"):
            (root / name).mkdir(mode=0o777)
            (root / name).chmod(0o777)
        context = root / "bot" / ".bcs" / "session.json"
        write(context, {"token": token if credential_kind in self._TOKEN_KINDS
                        else "", "bot_uuid": self.driver_id,
                        "api_base_url": self.origin})
        base = str(root)
        path_dirs = []
        docker_binary = shutil.which("docker")
        if docker_binary:
            path_dirs.append(str(Path(docker_binary).resolve().parent))
        environment = {"PATH": ":".join([*path_dirs, "/usr/bin", "/bin"]),
                       "HOME": base + "/bot", "BOT_DATA_DIR": base + "/bot",
                       "TMPDIR": base + "/tmp", "LANG": "C"}
        self._widen_launcher_mounts(root)
        docker_host = os.environ.get("DOCKER_HOST")
        if docker_host:
            environment["DOCKER_HOST"] = docker_host
        if credential_kind in self._COOKIE_KINDS:
            staged = os.environ.get(self.profile["auth"]["cookie_env"], "")
            if not staged:
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration CLI Human Cookie is absent")
            environment["BCS_COOKIE"] = staged
            environment["tc_sdb_nenv"] = "production"
        elif credential_kind == "human_cookie":
            environment["BCS_COOKIE"] = cookie
            environment["tc_sdb_nenv"] = "production"
        return root, context, environment

    @classmethod
    def _sweep_stale_launcher_roots(cls, *, max_age_seconds: float = 86400) -> None:
        """Remove abandoned simulator-wrapper roots from crashed runs."""
        try:
            candidates = sorted(cls._LAUNCHER_ROOT_BASE.glob("runner-*"),
                                key=lambda item: item.stat().st_mtime)
        except OSError:
            return
        now = time.time()
        for candidate in candidates:
            try:
                if now - candidate.stat().st_mtime < max_age_seconds:
                    break
                shutil.rmtree(candidate)
            except OSError:
                continue

    @staticmethod
    def _widen_launcher_mounts(root: Path) -> None:
        """Make the frozen CLI's bind mounts writable by the simulator uid."""
        for relative in ("home/.bcs", "home/.bcs/logs", "home/.bcs-mktmp",
                         "bot", "bot/.bcs", "bot/.bcs/logs", "bot/.bcs-mktmp"):
            target = root / relative
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o777)
        (root / "bot" / ".bcs" / "session.json").chmod(0o666)

    @staticmethod
    def _cleanup(context: Path, root: Path) -> str:
        status = "CLEARED"
        try:
            write(context, {"token": "", "bot_uuid": "", "api_base_url": ""})
            shutil.rmtree(root)
        except BaseException:
            status = "UNCONFIRMED"
        if root.exists():
            status = "UNCONFIRMED"
        return status

    def execute(self, operation: str, **kwargs) -> dict:
        if operation not in CUSTOM_COLLABORATION_CLI_OPERATIONS:
            raise CustomCollaborationCliRunnerError(
                "operation is not a reviewed custom collaboration CLI leaf")
        if operation in self.executed:
            raise CustomCollaborationCliRunnerError(
                "custom collaboration CLI operation was already executed")
        expected_index = CUSTOM_COLLABORATION_CLI_OPERATIONS.index(operation)
        if self.executed != list(CUSTOM_COLLABORATION_CLI_OPERATIONS[:expected_index]):
            raise CustomCollaborationCliRunnerError(
                "custom collaboration CLI operations cannot be reordered or skipped")
        role, arguments = self._contract(operation, **kwargs)
        exit_status = 0
        credential_kind, token, cookie = self._credentials()
        # Live preprod r1/r2: the station ACE gateway identity-gates the
        # validation route even though the BCS application serves it
        # unauthenticated (anonymous and driver-token requests are refused
        # with USER_NOT_LOGIN; the Human Cookie identity is accepted). The
        # remote validation leaf therefore presents the run's family
        # credential; the owned Singlebox face keeps its anonymous preflight.
        if operation == VALIDATE_YAML_OPERATION and self.tier == "local":
            credential_kind, token, cookie = "none", "", ""
        secrets = [value.encode() for value in (token, cookie) if value]
        root = context = environment = None
        primary: BaseException | None = None
        cleanup_status = "NOT_WRITTEN"
        started = time.monotonic()
        data = None
        try:
            root, context, environment = self._context(credential_kind, token, cookie)
            result = capture(
                [str(self.binary), "--url", self.origin, "--json",
                 "--log-level", "error", *arguments],
                cwd=root, environment=environment,
                timeout_seconds=self.profile["policy"]["timeout_seconds"],
                max_output_bytes=self.profile["policy"]["max_response_bytes"],
                collect_stderr=True)
            if any(secret in result.stdout or secret in result.stderr
                   for secret in secrets):
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration product CLI emitted its credential")
            if operation == TRIAL_RESPOND_GUARD_OPERATION:
                # The reviewed guard is proven by refusal: a nonzero exit is
                # the expected product behaviour, never a failure.
                if result.returncode == 0:
                    raise CustomCollaborationCliRunnerError(
                        "custom collaboration respond guard did not refuse")
                exit_status = result.returncode
                data = {"guard_refused": True, "node": "plan",
                        "exit_code": result.returncode,
                        "not_pending": b"not pending" in result.stderr}
            else:
                if result.returncode != 0:
                    raise CustomCollaborationCliRunnerError(
                        "custom collaboration product CLI returned nonzero: "
                        + (_failure_detail(result.stdout, result.stderr, secrets)
                           or "<no output>"))
                data = self._parse(operation, result.stdout, **kwargs)
        except ProcessCaptureError as error:
            public = CustomCollaborationCliRunnerError(
                "custom collaboration product CLI process failed")
            if getattr(error, "child_shutdown_status", None) == "UNCONFIRMED":
                public.child_shutdown_status = "UNCONFIRMED"
            primary = public
        except BaseException as error:
            primary = error
        finally:
            if context is not None and root is not None:
                cleanup_status = self._cleanup(context, root)
        secrets = []
        if cleanup_status != "CLEARED":
            failure = CustomCollaborationCliRunnerError(
                "custom collaboration CLI private context cleanup is unconfirmed")
            failure.credential_cleanup_status = cleanup_status
            if primary is None:
                primary = failure
            else:
                primary.credential_cleanup_status = cleanup_status
        if primary is not None:
            if isinstance(primary, KeyboardInterrupt):
                raise primary
            if not isinstance(primary, CustomCollaborationCliRunnerError):
                primary = CustomCollaborationCliRunnerError(
                    "custom collaboration product CLI output is invalid")
            raise primary
        self.executed.append(operation)
        return {"data": data, "evidence": {
            "transport": "PRODUCT_CLI", "operation": operation, "caller_role": role,
            "command_arguments": list(arguments),
            "http_method_declared": "CLI", "route_declared": "product-cli",
            "credential_kind": credential_kind, "exit_status": exit_status,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
            "remote_validation": "NOT_RUN", "grants_release_credit": False}}

    def _parse(self, operation: str, raw: bytes, **kwargs) -> object:
        if operation == VALIDATE_YAML_OPERATION:
            value = _json_object(raw, "YAML validation")
            summary = value.get("summary")
            if (value.get("valid") is not True or not isinstance(summary, dict)
                    or summary.get("participants") != 2 or summary.get("nodes") != 2):
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration YAML validation summary differs")
            return value
        if operation == CREATE_PERSISTENT_GROUP_OPERATION:
            value = _json_object(raw, "persistent Group creation")
            participants = value.get("participants")
            ids = participant_ids(participants)
            if (not isinstance(value.get("id"), str) or not value["id"]
                    or not isinstance(value.get("session_id"), str)
                    or not value["session_id"]
                    or value.get("driver_bot") != self.driver_id
                    or any(not isinstance(item, str) for item in (participants or []))
                    or set(ids) != {self.driver_id, self.member_id} or len(ids) != 2
                    or value.get("created") is not True):
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration persistent Group response differs")
            return value
        if operation == READ_PERSISTENT_GROUP_OPERATION:
            value = _json_object(raw, "persistent Group readback")
            ids = participant_ids(value.get("participants"))
            if (value.get("id") != kwargs["group_id"]
                    or value.get("driver_bot") != self.driver_id
                    or value.get("group_strategy") != "state_machine"
                    or sorted(ids) != sorted([self.driver_id, self.member_id])
                    or len(ids) != 2):
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration Group readback differs")
            return value
        if operation == TRIAL_CREATE_GROUP_OPERATION:
            # Converged trial transport: the reviewed projection equals the
            # persistent obligation leaf (bindings stored on the Group, the
            # initial Session handle returned with it).
            value = _json_object(raw, "trial Group creation")
            participants = value.get("participants")
            ids = participant_ids(participants)
            if (not isinstance(value.get("id"), str) or not value["id"]
                    or not isinstance(value.get("session_id"), str)
                    or not value["session_id"]
                    or value.get("driver_bot") != self.driver_id
                    or any(not isinstance(item, str) for item in (participants or []))
                    or set(ids) != {self.driver_id, self.member_id} or len(ids) != 2
                    or value.get("created") is not True):
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration trial Group response differs")
            return value
        if operation == TRIAL_QUERY_GRAPH_OPERATION:
            value = _json_object(raw, "execution graph")
            run, nodes = value.get("run"), value.get("nodes")
            if (not isinstance(run, dict) or run.get("run_id") != kwargs["run_id"]
                    or not isinstance(nodes, list) or not nodes
                    or any(not isinstance(node, dict)
                           or not isinstance(node.get("node_id"), str) for node in nodes)):
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration execution graph differs")
            if any(node.get("status") not in ("pending", "running", "completed",
                                              "skipped", "failed") for node in nodes):
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration execution graph status differs")
            return value
        if operation in (TRIAL_QUERY_PENDING_OPERATION, TRIAL_MESSAGES_OPERATION):
            value = _json_array(raw, "pending Human inputs" if operation
                                == TRIAL_QUERY_PENDING_OPERATION else "trial history")
            if operation == TRIAL_QUERY_PENDING_OPERATION:
                if value != []:
                    raise CustomCollaborationCliRunnerError(
                        "custom collaboration Bot-only Run must expose no pending "
                        "Human input")
                return value
            serialized = json.dumps(value, ensure_ascii=False)
            if ("bcsPanel.StateMachineRunView" not in serialized
                    or kwargs["run_id"] not in serialized):
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration restored history differs")
            return value
        if operation == TRIAL_ADD_MEMBER_OPERATION:
            value = _json_object(raw, "trial membership")
            rows = value.get("participants")
            if (not isinstance(rows, list)
                    or (value.get("session_id") or value.get("id")) != kwargs["session_id"]
                    or not any(isinstance(row, dict)
                               and (row.get("bot_uuid") or row.get("actor_id")) == kwargs["human_id"]
                               and row.get("role") == "consultant" for row in rows)):
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration trial membership projection differs")
            return value
        if operation == TRIAL_PERMISSION_OPERATION:
            value = _json_object(raw, "trial permission")
            if (value.get("allowed") is not True
                    or (value.get("session_id") or value.get("id")) != kwargs["session_id"]
                    or value.get("policy_version") != "session_state_machine_v1"
                    or value.get("group_strategy") != "chat"
                    or value.get("caller_bot_id") != self.driver_id):
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration trial permission projection differs")
            return value
        if operation == TRIAL_START_RUN_OPERATION:
            value = _json_object(raw, "one-shot Run start")
            run = value.get("run")
            if (not isinstance(run, dict)
                    or not isinstance(run.get("run_id"), str) or not run["run_id"]
                    or run.get("session_id") != kwargs["session_id"]
                    or not isinstance(run.get("group_id"), str) or not run["group_id"]
                    or run.get("status") not in ("running", "waiting")):
                raise CustomCollaborationCliRunnerError(
                    "custom collaboration one-shot Run start projection differs")
            return value
        raise CustomCollaborationCliRunnerError(
            "custom collaboration CLI leaf is outside the reviewed plan")
