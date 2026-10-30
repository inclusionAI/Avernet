"""Focused Direct-chat leaf vocabulary, profile validator and CLI runner.

The reviewed DM operation vocabulary, the declared route bindings, the
retained-effect relationships, the fail-closed dual-environment profile
validator (including the qualified preprod ``<bot_id>:<owner_id>`` actor
form gate and the bare local form gate) and the one-shot product-CLI
runner envelope live here so the dual case-run module stays under the
source-file budget.  The runner executes the reviewed DM leaves exactly
once, in order, inside the simulator-wrapper-style private launcher root
and never journals a credential.
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import time
import uuid

from .bounded_process import (
    ProcessCaptureError,
    capture,
    scrub_failure_text,
    stderr_failure_detail,
)
from .local_cli import LocalCliError, require_cli_binary
from .local_direct_chat_contract import REQUIREMENTS
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

DIRECT_CHAT_CLI_PROFILE_CONTRACT = "dual-direct-chat-cli-profile-v1"
DIRECT_CHAT_CLI_EFFECT = "TWO_OWNED_DIRECT_RUNS_CANCELLED_RETAINED_NO_GROUP_OR_SESSION_DELETE"

PING_MESSAGE = "ping"
MAIN_MESSAGE = "Prepare a release checklist"
MAIN_TIMEOUT_MS = 60000
MAIN_TAGS = ("e2e", "release")

DIRECT_CHAT_PING_SUBMIT_OPERATION = "direct_chat.ping.submit"
DIRECT_CHAT_MAIN_SUBMIT_OPERATION = "direct_chat.main.submit"
DIRECT_CHAT_MAIN_READ_OPERATION = "direct_chat.main.read"
DIRECT_CHAT_MAIN_CANCEL_OPERATION = "direct_chat.main.cancel"
DIRECT_CHAT_MAIN_TERMINAL_OPERATION = "direct_chat.main.terminal"
DIRECT_CHAT_PING_CANCEL_OPERATION = "direct_chat.ping.cancel"
DIRECT_CHAT_PING_TERMINAL_OPERATION = "direct_chat.ping.terminal"

DIRECT_CHAT_CLI_OPERATIONS = (
    DIRECT_CHAT_PING_SUBMIT_OPERATION,
    DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
    DIRECT_CHAT_MAIN_READ_OPERATION,
    DIRECT_CHAT_MAIN_CANCEL_OPERATION,
    DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
    DIRECT_CHAT_PING_CANCEL_OPERATION,
    DIRECT_CHAT_PING_TERMINAL_OPERATION,
)

DIRECT_CHAT_REQUIREMENTS = tuple(REQUIREMENTS)

DIRECT_CHAT_REQUIREMENT_OPERATIONS = {
    "DM.ping-submit": (DIRECT_CHAT_PING_SUBMIT_OPERATION,),
    "DM.main-submit": (DIRECT_CHAT_MAIN_SUBMIT_OPERATION,),
    "DM.main-read": (DIRECT_CHAT_MAIN_READ_OPERATION,),
    "DM.main-cancel": (DIRECT_CHAT_MAIN_CANCEL_OPERATION,),
    "DM.main-terminal": (DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
                         DIRECT_CHAT_PING_CANCEL_OPERATION,
                         DIRECT_CHAT_PING_TERMINAL_OPERATION),
}

DIRECT_CHAT_ADAPTER_METHODS = ("submit_ping_run", "submit_main_run",
                                "read_main_run", "cancel_main_run",
                                "read_main_terminal_run", "cancel_ping_run",
                                "read_ping_terminal_run")

DIRECT_CHAT_DECLARED_ROUTES = {
    DIRECT_CHAT_PING_SUBMIT_OPERATION: ("POST", "/bots/{member_actor_id}/chat-async"),
    DIRECT_CHAT_MAIN_SUBMIT_OPERATION: ("POST", "/bots/{member_actor_id}/chat-async"),
    DIRECT_CHAT_MAIN_READ_OPERATION: ("GET", "/chat/runs/{run_id}"),
    DIRECT_CHAT_MAIN_CANCEL_OPERATION: ("POST", "/chat/runs/{run_id}/cancel"),
    DIRECT_CHAT_MAIN_TERMINAL_OPERATION: ("GET", "/chat/runs/{run_id}"),
    DIRECT_CHAT_PING_CANCEL_OPERATION: ("POST", "/chat/runs/{run_id}/cancel"),
    DIRECT_CHAT_PING_TERMINAL_OPERATION: ("GET", "/chat/runs/{run_id}"),
}

_HTTP_PRODUCT_OPERATIONS = frozenset()

DIRECT_CHAT_CLI_RELATIONSHIPS = {
    "ping_run": "submitted-tracked-then-cancelled",
    "main_run": "submitted-read-cancelled-terminal-read",
    "run_store": "case-owned-async-chat-runs-retained",
    "cleanup": "exact-run-cancels-no-delete",
    "participant_mode": "driver-to-member-direct-target",
}


CHAT_RUN_STATES = ("pending", "submitted", "running", "completed", "failed", "cancelled", "unknown")


def direct_chat_cli_credential_kind(profile: dict) -> str:
    """Label the tier-aware credential family without echoing any secret."""
    if profile["environment"]["tier"] == "local":
        return "owned_local_bot_token"
    if profile["auth"]["kind"] == "human-cookie-with-owned-bot-tokens":
        return "human_cookie_with_owned_bot_tokens"
    return "human_cookie"


def expected_step_transport(operation: str) -> str:
    return ("PRODUCT_HTTP" if operation in _HTTP_PRODUCT_OPERATIONS
            else "PRODUCT_CLI")


def _env_ref(value: object, where: str) -> str:
    if (not isinstance(value, str)
            or not 3 <= len(value) <= 100
            or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in value)):
        raise ConfigError(f"{where}: expected an uppercase environment reference")
    return value


def _actor(value: object, role: str, human: str, tier: str) -> dict:
    actor = object_keys(value, {"bot_id", "owner_id", "dedicated", "visibility"},
                        f"actors.{role}")
    bot_id = identifier(actor["bot_id"], f"actors.{role}.bot_id")
    if tier == "preprod" and (
            bot_id.count(":") != 1 or not bot_id.rsplit(":", 1)[0]
            or not bot_id.rsplit(":", 1)[1].isdigit()):
        raise ConfigError(
            f"actors.{role}.bot_id: preprod direct chat requires the qualified "
            f"<bot_id>:<owner_id> station form")
    if tier != "preprod" and ":" in bot_id:
        raise ConfigError(
            f"actors.{role}.bot_id: local direct chat requires the bare bot id")
    if actor["owner_id"] != human or actor["dedicated"] is not True:
        raise ConfigError(f"actors.{role}: must be dedicated and owned by the configured Human")
    if actor["visibility"] != "public":
        raise ConfigError(f"actors.{role}.visibility: direct chat requires public Bots")
    return actor


def _validate_route_bindings(value: object, member_id: str) -> dict:
    if not isinstance(value, dict) or set(value) != set(DIRECT_CHAT_DECLARED_ROUTES):
        raise ConfigError("route_bindings: direct chat requires the seven reviewed DM leaves")
    result = {}
    for operation, (method, path) in DIRECT_CHAT_DECLARED_ROUTES.items():
        binding = object_keys(value[operation], {
            "method", "path", "api_face", "auth", "response", "traceparent",
        }, f"route_bindings.{operation}")
        if (binding["method"] != method or binding["path"] != path
                or binding["api_face"] != "legacy"
                or binding["auth"] != "case_owned_driver"
                or binding["response"] != "legacy-json"
                or binding["traceparent"] != "disabled"):
            raise ConfigError(
                f"route_bindings.{operation}: binding differs from the DM migration contract")
        result[operation] = dict(binding)
    if identifier(member_id, "actors.member.bot_id") != member_id:
        raise ConfigError("direct chat route bindings driver target is invalid")
    return result


def validate_direct_chat_cli_profile(raw: object) -> dict:
    """Validate one dual-environment Direct-chat product-CLI Profile."""
    if not isinstance(raw, dict) and not hasattr(raw, "get"):
        raise ConfigError("direct chat CLI profile must be an object")
    preview_environment = raw.get("environment") if isinstance(raw, dict) else None
    tier = (preview_environment.get("tier")
            if isinstance(preview_environment, dict) else None)
    if tier not in {"local", "preprod"}:
        raise ConfigError("environment.tier: only Singlebox and preprod are admitted")
    expected_keys = ({"schema_version", "contract", "environment", "entrypoint",
                      "human", "actors", "auth", "relationships", "policy", "cli",
                      "route_bindings", "browser_origin"}
                     if tier == "preprod" else
                     {"schema_version", "contract", "environment", "entrypoint",
                      "human", "actors", "auth", "relationships", "policy", "cli",
                      "route_bindings"})
    profile = object_keys(raw, expected_keys, "direct chat CLI profile")
    if (type(profile["schema_version"]) is not int
            or profile["schema_version"] != 1
            or profile["contract"] != DIRECT_CHAT_CLI_PROFILE_CONTRACT):
        raise ConfigError("direct chat CLI profile: unsupported version or contract")
    environment = object_keys(profile["environment"],
                              {"tier", "id", "expected_bcs_env"},
                              "direct chat CLI environment")
    identifier(environment["id"], "environment.id")
    if environment["expected_bcs_env"] != environment["tier"]:
        if environment["tier"] != "preprod" or environment["expected_bcs_env"] != "pre":
            raise ConfigError("environment.expected_bcs_env: environment binding mismatch")
    entrypoint = object_keys(profile["entrypoint"],
                             {"kind", "origin", "api_face", "manifest_face"},
                             "direct chat CLI entrypoint")
    if entrypoint["api_face"] != "legacy" or entrypoint["manifest_face"] != "legacy":
        raise ConfigError("entrypoint: direct chat CLI requires the legacy product face")
    target = parsed_origin(entrypoint["origin"], environment["tier"])
    if environment["tier"] == "local":
        if (entrypoint["kind"] != "singlebox" or target.scheme != "http"
                or target.hostname not in {"127.0.0.1", "localhost", "::1"}
                or not target.port):
            raise ConfigError("entrypoint: local direct chat CLI requires an owned loopback")
    else:
        if (entrypoint["kind"] != "gateway" or target.scheme != "https"
                or target.hostname != "bcs-pre.alipay.com"):
            raise ConfigError(
                "entrypoint: preprod direct chat CLI requires the admitted HTTPS "
                "gateway bcs-pre.alipay.com")
    human = object_keys(profile["human"], {"user_id"}, "human")
    identifier(human["user_id"], "human.user_id")
    actors = object_keys(profile["actors"], {"driver", "member"}, "actors")
    actors["driver"] = _actor(actors["driver"], "driver", human["user_id"], tier)
    actors["member"] = _actor(actors["member"], "member", human["user_id"], tier)
    if actors["driver"]["bot_id"] == actors["member"]["bot_id"]:
        raise ConfigError("actors: direct chat CLI requires two distinct dedicated Bots")
    if environment["tier"] == "local":
        auth = object_keys(profile["auth"], {"kind", "driver_token_env"}, "auth")
        if auth["kind"] != "owned-local-bot-tokens":
            raise ConfigError(
                "auth.kind: local direct chat CLI requires the owned driver Bot token")
        _env_ref(auth["driver_token_env"], "auth.driver_token_env")
    else:
        auth = profile["auth"]
        if (isinstance(auth, dict) and auth.get("kind") == "human-cookie"
                and set(auth) == {"kind", "cookie_env"}):
            _env_ref(auth["cookie_env"], "auth.cookie_env")
        elif (isinstance(auth, dict)
                and auth.get("kind") == "human-cookie-with-owned-bot-tokens"
                and set(auth) == {"kind", "driver_token_env", "member_token_env",
                                  "cookie_env"}):
            _env_ref(auth["driver_token_env"], "auth.driver_token_env")
            _env_ref(auth["member_token_env"], "auth.member_token_env")
            _env_ref(auth["cookie_env"], "auth.cookie_env")
        else:
            raise ConfigError(
                "auth.kind: preprod direct chat CLI requires the Human Cookie or "
                "the reviewed ACE trio (Human Cookie plus the owned driver and "
                "member Bot tokens)")
    relationships = object_keys(profile["relationships"],
                                set(DIRECT_CHAT_CLI_RELATIONSHIPS), "relationships")
    if relationships != DIRECT_CHAT_CLI_RELATIONSHIPS:
        raise ConfigError("relationships: retained Direct-chat effect admission differs")
    policy = object_keys(profile["policy"],
                         {"fixture_pool", "exclusive_use_confirmed", "timeout_seconds",
                          "max_response_bytes", "failure_retention_hours"}, "policy")
    identifier(policy["fixture_pool"], "policy.fixture_pool")
    if (type(policy["exclusive_use_confirmed"]) is not bool
            or policy["exclusive_use_confirmed"] is not True):
        raise ConfigError(
            "policy.exclusive_use_confirmed: retained Direct-chat runs require exclusive use")
    for key, maximum in (("timeout_seconds", 30), ("max_response_bytes", 65536),
                         ("failure_retention_hours", 168)):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ConfigError(f"policy.{key}: integer outside the CLI contract")
    cli = object_keys(profile["cli"], {"transport", "command_family", "api_face"}, "cli")
    if cli != {"transport": "product-cli", "command_family": "chat", "api_face": "legacy"}:
        raise ConfigError("cli: product CLI binding differs from the chat command family")
    if environment["tier"] == "preprod":
        origin(profile["browser_origin"], "preprod browser_origin")
    profile["route_bindings"] = _validate_route_bindings(
        profile["route_bindings"], actors["member"]["bot_id"])
    return json.loads(json.dumps(profile, allow_nan=False), object_pairs_hook=unique_keys)


class DirectChatCliRunnerError(WorkspaceCliRunnerError):
    pass


class DirectChatCliRunner:
    """Execute the seven reviewed DM product-CLI leaves once, in order.

    The launcher envelope mirrors the fixed simulator-wrapper form: a private
    ``~/.bcs-e2e/runner-<hex>`` root widened for the simulator uid, a docker
    directory on the child PATH, ``DOCKER_HOST`` forwarding, and a CLEARED
    private-context cleanup that never journals credentials.
    """

    _LAUNCHER_ROOT_BASE = Path.home() / ".bcs-e2e"

    def __init__(self, profile: dict, *, binary: Path, binary_sha256: str,
                 state_dir: Path):
        self.profile = validate_direct_chat_cli_profile(profile)
        if (not isinstance(state_dir, Path) or not state_dir.is_absolute()
                or state_dir.is_symlink() or not state_dir.is_dir()):
            raise DirectChatCliRunnerError(
                "direct chat CLI requires an absolute current Run directory")
        try:
            require_cli_binary(binary, binary_sha256)
        except LocalCliError as error:
            raise DirectChatCliRunnerError("direct chat CLI binary is not approved") from None
        self.binary = Path(binary).resolve()
        self.binary_sha256 = binary_sha256
        self.state_dir = state_dir
        self.origin = self.profile["entrypoint"]["origin"]
        self.tier = self.profile["environment"]["tier"]
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.executed: list[str] = []
        self._sweep_stale_launcher_roots()

    def _credentials(self) -> tuple[str, str, str]:
        """Return (credential_kind, session token, cookie); never a secret label."""
        if self.tier == "local":
            value = os.environ.get(self.profile["auth"]["driver_token_env"], "")
            if not value:
                raise DirectChatCliRunnerError(
                    "direct chat CLI owned driver Bot credential is absent")
            return "owned_local_bot_token", value, ""
        auth = self.profile["auth"]
        if auth["kind"] == "human-cookie-with-owned-bot-tokens":
            tokens = tuple(os.environ.get(auth[key], "")
                           for key in ("driver_token_env", "member_token_env",
                                       "cookie_env"))
            token, _, cookie = tokens
            if not all(tokens):
                raise DirectChatCliRunnerError(
                    "direct chat CLI ACE trio (driver and member Bot tokens plus "
                    "the Human Cookie) is absent")
            return "human_cookie_with_owned_bot_tokens", tokens[0], tokens[2]
        cookie = os.environ.get(auth["cookie_env"], "")
        if not cookie:
            raise DirectChatCliRunnerError("direct chat CLI Human Cookie is absent")
        return "human_cookie", "", cookie

    def _contract(self, operation: str, *, message: object = None,
                  tags: object = None, timeout_ms: object = None,
                  run_id: object = None, bot_uuid: object = None):
        if operation in (DIRECT_CHAT_PING_SUBMIT_OPERATION,
                         DIRECT_CHAT_MAIN_SUBMIT_OPERATION):
            expected_message = (PING_MESSAGE if operation == DIRECT_CHAT_PING_SUBMIT_OPERATION
                                else MAIN_MESSAGE)
            if message != expected_message:
                raise DirectChatCliRunnerError(
                    "direct chat submit requires its exact reviewed message")
            if not isinstance(bot_uuid, str) or bot_uuid != self.member_id:
                raise DirectChatCliRunnerError(
                    "direct chat submit requires the exact PM Bot target")
            arguments = ["chat", "--bot-uuid", self.member_id,
                         "--message", message]
            if operation == DIRECT_CHAT_MAIN_SUBMIT_OPERATION:
                if tags != list(MAIN_TAGS):
                    raise DirectChatCliRunnerError(
                        "direct chat main submit requires its exact reviewed tags")
                if timeout_ms != MAIN_TIMEOUT_MS:
                    raise DirectChatCliRunnerError(
                        "direct chat main submit requires its exact reviewed timeout")
                arguments.extend(["--timeout-ms", str(MAIN_TIMEOUT_MS)])
                for tag in MAIN_TAGS:
                    arguments.extend(["--tag", tag])
            elif tags is not None or timeout_ms is not None:
                raise DirectChatCliRunnerError(
                    "direct chat ping submit admits no tags or timeout budget")
            arguments.append("--detach")
            return ("driver", "POST", "/bots/{member_actor_id}/chat-async",
                    tuple(arguments))
        if not isinstance(run_id, str) or not run_id:
            raise DirectChatCliRunnerError(
                "direct chat run leaf requires the exact Run ID")
        identifier(run_id, "direct chat Run ID")
        if operation in (DIRECT_CHAT_MAIN_READ_OPERATION,
                         DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
                         DIRECT_CHAT_PING_TERMINAL_OPERATION):
            return ("driver", "GET", "/chat/runs/{run_id}",
                    ("chat-run", "status", "--run-id", run_id))
        if operation in (DIRECT_CHAT_MAIN_CANCEL_OPERATION,
                         DIRECT_CHAT_PING_CANCEL_OPERATION):
            return ("driver", "POST", "/chat/runs/{run_id}/cancel",
                    ("chat-run", "cancel", "--run-id", run_id))
        raise DirectChatCliRunnerError("unsupported direct chat CLI operation")

    def _context(self, credential_kind: str, token: str, cookie: str):
        root = self._LAUNCHER_ROOT_BASE / f"runner-{uuid.uuid4().hex}"
        root.mkdir(mode=0o700, parents=True)
        for name in ("home", "bot", "tmp"):
            (root / name).mkdir(mode=0o777)
            (root / name).chmod(0o777)
        context = root / "bot" / ".bcs" / "session.json"
        write(context, {"token": token if credential_kind != "human_cookie" else "",
                        "bot_uuid": self.driver_id, "api_base_url": self.origin})
        docker_binary = shutil.which("docker")
        path_dirs = ([str(Path(docker_binary).resolve().parent)]
                     if docker_binary else [])
        environment = {"PATH": ":".join([*path_dirs, "/usr/bin", "/bin"]),
                       "HOME": str(root / "bot"), "BOT_DATA_DIR": str(root / "bot"),
                       "TMPDIR": str(root / "tmp"), "LANG": "C"}
        self._widen_launcher_mounts(root)
        docker_host = os.environ.get("DOCKER_HOST")
        if docker_host:
            environment["DOCKER_HOST"] = docker_host
        if self.tier == "local":
            environment["SERVER_ENV"] = "local"
            environment["tc_sdb_nenv"] = "production"
        elif credential_kind != "owned_local_bot_token":
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

    @staticmethod
    def _load_json_output(raw: bytes, where: str) -> dict:
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
        except (UnicodeError, json.JSONDecodeError):
            raise DirectChatCliRunnerError(
                f"direct chat CLI {where} did not emit one JSON value") from None
        if not isinstance(value, dict):
            raise DirectChatCliRunnerError(
                f"direct chat CLI {where} JSON output is not an object")
        return value

    def execute(self, operation: str, *, message: object = None,
                tags: object = None, timeout_ms: object = None,
                run_id: object = None, bot_uuid: object = None) -> dict:
        if operation not in DIRECT_CHAT_CLI_OPERATIONS:
            raise DirectChatCliRunnerError(
                "operation is not a reviewed direct chat CLI leaf")
        if operation in self.executed:
            raise DirectChatCliRunnerError(
                "direct chat CLI operation was already executed")
        expected_index = DIRECT_CHAT_CLI_OPERATIONS.index(operation)
        if self.executed != list(DIRECT_CHAT_CLI_OPERATIONS[:expected_index]):
            raise DirectChatCliRunnerError(
                "direct chat CLI operations cannot be reordered or skipped")
        role, method, path, arguments = self._contract(
            operation, message=message, tags=tags, timeout_ms=timeout_ms,
            run_id=run_id, bot_uuid=bot_uuid)
        credential_kind, token, cookie = self._credentials()
        secrets = [value.encode() for value in (token, cookie) if value]
        root = context = environment = None
        primary: BaseException | None = None
        cleanup_status = "NOT_WRITTEN"
        started = time.monotonic()
        data = None
        try:
            root, context, environment = self._context(credential_kind, token, cookie)
            command = [str(self.binary), "--url", self.origin, "--json",
                       "--log-level", "error", *arguments]
            result = capture(
                command, cwd=root, environment=environment,
                timeout_seconds=self.profile["policy"]["timeout_seconds"],
                max_output_bytes=self.profile["policy"]["max_response_bytes"],
                collect_stderr=True)
            if result.returncode != 0:
                detail = scrub_failure_text(
                    result.stdout.strip().decode("utf-8", "replace"),
                    secrets)[:500]
                stderr_tail = (stderr_failure_detail(result, secrets)
                               or "<no stderr>")
                raise DirectChatCliRunnerError("direct chat product CLI returned "
                    "nonzero: " + (detail or "<no output>") + " | stderr: "
                    + stderr_tail)
            raw = result.stdout
            if any(secret in raw for secret in secrets):
                raise DirectChatCliRunnerError(
                    "direct chat product CLI emitted its credential")
            if operation in (DIRECT_CHAT_PING_SUBMIT_OPERATION,
                             DIRECT_CHAT_MAIN_SUBMIT_OPERATION):
                data = self._parse_submit_output(raw)
            elif operation in (DIRECT_CHAT_MAIN_READ_OPERATION,
                               DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
                               DIRECT_CHAT_PING_TERMINAL_OPERATION):
                data = self._parse_status_output(raw)
            else:
                data = self._parse_cancel_output(raw)
        except ProcessCaptureError as error:
            public = DirectChatCliRunnerError("direct chat product CLI process failed")
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
            failure = DirectChatCliRunnerError(
                "direct chat CLI private context cleanup is unconfirmed")
            failure.credential_cleanup_status = cleanup_status
            if primary is None:
                primary = failure
            else:
                primary.credential_cleanup_status = cleanup_status
        if primary is not None:
            if isinstance(primary, KeyboardInterrupt):
                raise primary
            if not isinstance(primary, DirectChatCliRunnerError):
                primary = DirectChatCliRunnerError(
                    "direct chat product CLI output is invalid")
            raise primary
        self.executed.append(operation)
        evidence = {
            "transport": "PRODUCT_CLI",
            "operation": operation,
            "caller_role": role,
            "command_arguments": list(arguments),
            "http_method_declared": method,
            "route_declared": path,
            "credential_kind": credential_kind,
            "exit_status": 0,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
            "fixture_effect": DIRECT_CHAT_CLI_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        }
        return {"data": data, "evidence": evidence}

    _SUBMIT_CONTRACT = {"delivered", "submitted", "bot_uuid", "run_id",
                        "session_id", "state", "delivery_status", "wait_reason",
                        "error_message"}
    _STATUS_CONTRACT = {"run_id", "bot_uuid", "from_bot_id", "session_id", "state",
                        "response", "error_message", "created_at_ms", "updated_at_ms",
                        "expires_at_ms", "version", "content_truncated", "is_terminal"}
    _CANCEL_CONTRACT = {"run_id", "cancelled", "state", "response", "error_message",
                        "version", "content_truncated"}

    def _parse_projection(self, raw: bytes, where: str, contract: set[str],
                          exact: bool = True) -> dict:
        value = self._load_json_output(raw, where)
        keys = set(value)
        invalid = keys != contract if exact else not contract <= keys
        if invalid:
            raise DirectChatCliRunnerError(
                f"direct chat {where} projection differs from the product contract")
        return dict(value)

    def _parse_submit_output(self, raw: bytes) -> dict:
        value = self._parse_projection(raw, "detached submit", self._SUBMIT_CONTRACT)
        if (type(value.get("delivered")) is not bool
                or type(value.get("submitted")) is not bool
                or value["submitted"] is not True
                or value.get("error_message") is not None):
            raise DirectChatCliRunnerError(
                "direct chat detached submit admission differs "
                "from the product contract")
        if (not isinstance(value.get("bot_uuid"), str)
                or value["bot_uuid"] != self.member_id):
            raise DirectChatCliRunnerError(
                "direct chat detached submit does not target the exact PM Bot")
        for key in ("run_id", "session_id", "state"):
            if not isinstance(value.get(key), str) or not value[key]:
                raise DirectChatCliRunnerError(
                    f"direct chat detached submit {key} differs "
                    "from the product contract")
        if value["state"] not in {"pending", "submitted"}:
            raise DirectChatCliRunnerError(
                "direct chat detached submit state differs from the product contract")
        return value

    def _parse_status_output(self, raw: bytes) -> dict:
        value = self._parse_projection(raw, "run status", self._STATUS_CONTRACT, exact=False)
        if (not isinstance(value["run_id"], str) or not value["run_id"]
                or value["state"] not in CHAT_RUN_STATES
                or type(value.get("is_terminal")) is not bool
                or type(value.get("version")) is not int or value["version"] <= 0
                or not isinstance(value.get("response"), dict)
                or not isinstance(value["response"].get("content"), str)):
            raise DirectChatCliRunnerError(
                "direct chat run status projection differs from the product contract")
        return value

    def _parse_cancel_output(self, raw: bytes) -> dict:
        # The station acknowledges cancel asynchronously: the ack may carry
        # ``cancelled=false`` with the Run still running (or already completed
        # naturally), while the downstream abort converges afterwards.  The CLI
        # help states a downstream abort may remain unconfirmed; the leaf
        # therefore validates the ack shape only, and the dual-environment
        # adapter enforces terminal-state convergence for the remote tier.
        value = self._parse_projection(raw, "run cancel", self._CANCEL_CONTRACT, exact=False)
        if (not isinstance(value["run_id"], str) or not value["run_id"]
                or type(value.get("cancelled")) is not bool
                or value["state"] not in CHAT_RUN_STATES
                or type(value.get("version")) is not int or value["version"] <= 0
                or not isinstance(value.get("response"), dict)):
            raise DirectChatCliRunnerError(
                "direct chat run cancel projection differs from the product contract")
        return value

