"""Bounded product-CLI runner for reviewed Workspace and Session-file slices."""

from __future__ import annotations

from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
from .bounded_process import ProcessCaptureError, capture
from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_cli import require_cli_binary
from .local_session_contract import session_source_binding
from .profile import identifier, unique_keys
from .run_state import RunStateError, write
from .workspace_cli_commands import (
    GROUP_CREATE_OPERATIONS,
    WorkspaceCliCommandContext,
    WorkspaceCliRunnerError,
    build_contract,
)
from .write_transport import WriteClient
from .workspace_cli_profile import (
    WORKSPACE_CLI_LIST_PROFILE_CONTRACT,
    WORKSPACE_CLI_STATUS_PROFILE_CONTRACT,
    WORKSPACE_CLI_DETAIL_PROFILE_CONTRACT,
    WORKSPACE_CLI_FUSE_PROFILE_CONTRACT,
    WORKSPACE_CLI_MEMBER_PROFILE_CONTRACT,
    SESSION_FILE_CORE_PROFILE_CONTRACT,
    validate_workspace_cli_list_profile,
    validate_workspace_cli_profile,
    validate_workspace_cli_status_profile,
    validate_workspace_cli_detail_profile,
    validate_workspace_cli_fuse_profile,
    validate_workspace_cli_member_profile,
    validate_session_file_core_profile,
)
from .session_cli_profile import (
    SESSION_CLI_CREATE_GET_PROFILE_CONTRACT,
    SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT,
    SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT,
    SESSION_CLI_COLLECTION_PROFILE_CONTRACT,
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_GROUP_INVITE_CREATE_OPERATION,
    SESSION_CLI_GROUP_INVITE_JOIN_OPERATION,
    validate_session_cli_create_get_profile,
    validate_session_cli_lifecycle_profile,
    validate_session_cli_complete_invoke_profile,
    validate_session_cli_collection_profile,
)


CREATE_GET_EFFECT = "RETAINED_DYNAMIC_GROUP_AND_INITIAL_SESSION"
STATUS_EFFECT = "RETAINED_DYNAMIC_GROUPS_AND_INITIAL_SESSIONS"
FUSE_EFFECT = "RETAINED_DYNAMIC_GROUP_INITIAL_SESSION_AND_FUSION"
WORKSPACE_CLI_MEMBER_EFFECT = "RETAINED_DYNAMIC_GROUP_INITIAL_SESSION_AND_MEMBER"
SESSION_FILE_CORE_EFFECT = "RETAINED_DYNAMIC_GROUP_SESSIONS_AND_FILE"
SESSION_CLI_CREATE_GET_EFFECT = "OWNED_GROUP_SESSIONS_READ_THEN_GENERIC_GROUP_DELETE"
SESSION_CLI_LIFECYCLE_EFFECT = "OWNED_SESSION_LIFECYCLE_THEN_GENERIC_GROUP_DELETE"
SESSION_CLI_COMPLETE_INVOKE_EFFECT = (
    "OWNED_SERVICE_SIGNOFF_AND_INVOCATION_THEN_GENERIC_GROUP_DELETE")


class WorkspaceCliRunner:
    """Execute only reviewed product-CLI Group leaves once, in order."""

    def __init__(self, profile: dict, *, binary: Path, binary_sha256: str, state_dir: Path):
        raw_contract = profile.get("contract") if isinstance(profile, dict) else None
        validators = {
            WORKSPACE_CLI_STATUS_PROFILE_CONTRACT: validate_workspace_cli_status_profile,
            WORKSPACE_CLI_LIST_PROFILE_CONTRACT: validate_workspace_cli_list_profile,
            WORKSPACE_CLI_DETAIL_PROFILE_CONTRACT: validate_workspace_cli_detail_profile,
            WORKSPACE_CLI_FUSE_PROFILE_CONTRACT: validate_workspace_cli_fuse_profile,
            WORKSPACE_CLI_MEMBER_PROFILE_CONTRACT: validate_workspace_cli_member_profile,
            SESSION_FILE_CORE_PROFILE_CONTRACT: validate_session_file_core_profile,
            SESSION_CLI_CREATE_GET_PROFILE_CONTRACT: validate_session_cli_create_get_profile,
            SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT: validate_session_cli_lifecycle_profile,
            SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT: validate_session_cli_complete_invoke_profile,
            SESSION_CLI_COLLECTION_PROFILE_CONTRACT: validate_session_cli_collection_profile,
        }
        validator = validators.get(raw_contract, validate_workspace_cli_profile) if isinstance(raw_contract, str) else validate_workspace_cli_profile
        self.profile = validator(profile)
        if (not isinstance(state_dir, Path) or not state_dir.is_absolute()
                or state_dir.is_symlink() or not state_dir.is_dir()):
            raise WorkspaceCliRunnerError("workspace CLI requires an absolute existing Run directory")
        require_cli_binary(binary, binary_sha256)
        self.binary = Path(binary).resolve()
        self.binary_sha256 = binary_sha256
        self.state_dir = state_dir
        self.origin = self.profile["entrypoint"]["origin"]
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.session_file_source = state_dir / "bcs-e2e-session-file-source.txt"
        self.session_file_download = state_dir / "bcs-e2e-session-file-download.txt"
        self.command_context = WorkspaceCliCommandContext(
            profile_contract=self.profile["contract"],
            driver_id=self.driver_id,
            member_id=self.member_id,
            session_file_source=self.session_file_source,
            session_file_download=self.session_file_download,
        )
        expected_source = b"bcs-e2e-session-file-payload\n"
        if self.session_file_source.exists():
            if self.session_file_source.read_bytes() != expected_source:
                raise WorkspaceCliRunnerError("session-file source input drifted")
        else:
            self.session_file_source.write_bytes(expected_source)
        if self.session_file_download.exists():
            raise WorkspaceCliRunnerError("session-file download target already exists")
        self.executed: list[str] = []
        self._sweep_stale_launcher_roots()

    def _credentials(self):
        if self.profile["contract"] == SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT:
            if (self.profile["environment"]["tier"] == "preprod"
                    and self.profile["auth"]["kind"] == "human-cookie-with-owned-bot-tokens"):
                value = os.environ.get(self.profile["auth"]["driver_token_env"], "")
                cookie = os.environ.get(self.profile["auth"]["cookie_env"], "")
                if not value or not cookie:
                    raise WorkspaceCliRunnerError(
                        "workspace CLI completion-invocation ACE trio (owned "
                        "driver Bot token plus Human Cookie) is absent")
                return "human_cookie_with_owned_bot_tokens", value
            value = os.environ.get(self.profile["auth"]["driver_token_env"], "")
            if not value:
                raise WorkspaceCliRunnerError("workspace CLI owned Bot credential is absent")
            return "owned_local_bot_token", value
        if (self.profile["contract"] in {WORKSPACE_CLI_LIST_PROFILE_CONTRACT,
                                         WORKSPACE_CLI_STATUS_PROFILE_CONTRACT}
                and self.profile["environment"]["tier"] == "preprod"):
            value = os.environ.get(self.profile["auth"]["driver_token_env"], "")
            cookie = os.environ.get(self.profile["auth"]["cookie_env"], "")
            if not value or not cookie:
                raise WorkspaceCliRunnerError(
                    "workspace CLI owned Bot token and Human Cookie session "
                    "bundle is absent")
            return "human_cookie_with_owned_driver_bot_token", value
        if self.profile["environment"]["tier"] == "local":
            value = os.environ.get(self.profile["auth"]["driver_token_env"], "")
            if not value:
                raise WorkspaceCliRunnerError("workspace CLI owned Bot credential is absent")
            return "owned_local_bot_token", value
        if (self.profile["contract"] == SESSION_FILE_CORE_PROFILE_CONTRACT
                and self.profile["auth"]["kind"] == "human-cookie-with-owned-driver-bot-token"):
            value = os.environ.get(self.profile["auth"]["driver_token_env"], "")
            cookie = os.environ.get(self.profile["auth"]["cookie_env"], "")
            if not value or not cookie:
                raise WorkspaceCliRunnerError("workspace CLI Session-file credential bundle is absent")
            return "human_cookie_with_owned_driver_bot_token", value
        value = os.environ.get(self.profile["auth"]["cookie_env"], "")
        if not value:
            raise WorkspaceCliRunnerError("workspace CLI Human Cookie is absent")
        return "human_cookie", value

    def _context(self, credential_kind: str, credential: str):
        root = Path.home() / ".bcs-e2e" / f"runner-{uuid.uuid4().hex}"
        root.mkdir(mode=0o700, parents=True)
        for name in ("home", "bot", "tmp"):
            (root / name).mkdir(mode=0o777)
            (root / name).chmod(0o777)
        context = root / "bot" / ".bcs" / "session.json"
        data = {
            "token": credential if credential_kind in {
                "owned_local_bot_token", "owned_bot_token",
                "human_cookie_with_owned_driver_bot_token",
                "human_cookie_with_owned_bot_tokens",
            } else "",
            "bot_uuid": self.driver_id,
            "api_base_url": self.origin,
        }
        write(context, data)
        base = str(root)
        path_dirs = []
        docker_binary = shutil.which("docker")
        if docker_binary:
            path_dirs.append(str(Path(docker_binary).resolve().parent))
        # HOME telescopes into BOT_DATA_DIR: the container uid cannot create
        # $HOME-relative caches on the docker-created home directory, while
        # BOT_DATA_DIR is bind-mounted writable; the CLI keeps its session
        # journal and OAuth2 storage there (same layout as its context seed).
        environment = {"PATH": ":".join([*path_dirs, "/usr/bin", "/bin"]),
                       "HOME": base + "/bot",
                       "BOT_DATA_DIR": base + "/bot", "TMPDIR": base + "/tmp",
                       "LANG": "C"}
        self._widen_launcher_mounts(root)
        docker_host = os.environ.get("DOCKER_HOST")
        if docker_host:
            environment["DOCKER_HOST"] = docker_host
        if credential_kind in {"human_cookie_with_owned_driver_bot_token",
                               "human_cookie_with_owned_bot_tokens"}:
            cookie = os.environ.get(self.profile["auth"]["cookie_env"], "")
            if not cookie:
                raise WorkspaceCliRunnerError("workspace CLI Human Cookie is absent")
            environment["BCS_COOKIE"] = cookie
            environment["tc_sdb_nenv"] = "production"
        elif credential_kind == "human_cookie":
            environment["BCS_COOKIE"] = credential
            environment["tc_sdb_nenv"] = "production"
        return root, context, environment

    _LAUNCHER_ROOT_BASE = Path.home() / ".bcs-e2e"

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
        """Make the frozen CLI's bind mounts writable by the simulator uid.

        The reviewed product-CLI distribution for the dual singlebox/preprod
        runs is the host wrapper pinned by the run receipt: it execs
        ``docker run --network host`` with the bcs:simulator image (uid 1000
        inside) and bind-mounts ``$HOME/.bcs`` and ``$BOT_DATA_DIR``.  Colima
        shares only the workstation ``$HOME`` by default, so the private run
        root lives under ``~/.bcs-e2e`` and its mounted subtrees are widened
        for the container uid.  The root itself stays mode 0700 and owned by
        the workstation user, so the widened subtree stays unreachable for
        other local users.
        """
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

    def _parse_create_output(self, raw: bytes) -> dict:
        text = raw.decode("utf-8")
        group_id = self._line(text, "ID")
        session_id = self._line(text, "Session")
        driver = self._line(text, "Driver")
        participants = self._line(text, "Participants").split(",")
        if (group_id != identifier(group_id, "workspace CLI Group ID")
                or session_id != identifier(session_id, "workspace CLI initial Session ID")
                or not session_id.startswith(group_id + ":")
                or driver != self.driver_id
                or len(participants) != 2
                or [value.strip() for value in participants] != [self.driver_id, self.member_id]):
            raise WorkspaceCliRunnerError("workspace CLI create output differs from the product contract")
        return {
            "group_id": group_id,
            "driver_bot": driver,
            "participants": [value.strip() for value in participants],
            "session_id": session_id,
        }

    @staticmethod
    def _line(text: str, label: str) -> str:
        lines = re.findall(rf"^  {re.escape(label)}: (.*)$", text, re.M)
        if len(lines) != 1 or not lines[0].strip():
            raise WorkspaceCliRunnerError("workspace CLI create output misses its bounded line contract")
        return lines[0].strip()

    def _parse_add_member_output(self, raw: bytes) -> dict:
        text = raw.decode("utf-8")
        if not text.startswith("✓ Member added to group:\n"):
            raise WorkspaceCliRunnerError("workspace CLI add-member output prefix is invalid")
        group = self._line(text, "Group")
        bot = self._line(text, "Bot")
        if (identifier(group, "workspace CLI add-member Group ID") != group
                or bot != self.member_id):
            raise WorkspaceCliRunnerError("workspace CLI add-member output differs from the product contract")
        return {"group": group, "bot": bot}

    @staticmethod
    def _parse_fuse_output(raw: bytes) -> dict:
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise WorkspaceCliRunnerError("workspace CLI fuse output is not UTF-8") from None
        prefix = "Fusion result:\n"
        if not text.startswith(prefix):
            raise WorkspaceCliRunnerError("workspace CLI fuse output prefix is invalid")
        return WorkspaceCliRunner._parse_json_output(text[len(prefix):].encode())

    @staticmethod
    def _load_json_output(raw: bytes):
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
        except (UnicodeError, json.JSONDecodeError):
            raise WorkspaceCliRunnerError("workspace CLI JSON output is invalid") from None
        return value

    @staticmethod
    def _parse_delete_output(raw: bytes):
        value = WorkspaceCliRunner._load_json_output(raw)
        if value is not None and value != {"deleted": True}:
            raise WorkspaceCliRunnerError("workspace CLI delete acknowledgement is invalid")
        return value

    @staticmethod
    def _parse_json_output(raw: bytes):
        value = WorkspaceCliRunner._load_json_output(raw)
        if not isinstance(value, dict):
            raise WorkspaceCliRunnerError("workspace CLI JSON output is not an object")
        return value

    @staticmethod
    def _parse_service_wait_timeout_output(raw: bytes, session_id: str) -> dict | None:
        """Project only the reviewed bounded-timeout contract, never raw stderr."""
        try:
            message = raw.decode("utf-8")
        except UnicodeError:
            return None
        if (not isinstance(session_id, str) or not message
                or "Timed out after" not in message
                or session_id not in message):
            return None
        return {
            "status": "timeout",
            "session_id": session_id,
            "timeout_ms": 1,
            "timeout_contract": "session-specific-bounded-timeout",
        }

    @staticmethod
    def _parse_session_messages_output(raw: bytes):
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
        except (UnicodeError, json.JSONDecodeError):
            raise WorkspaceCliRunnerError("workspace CLI messages output is invalid") from None
        if (not isinstance(value, list) or not all(isinstance(item, dict) for item in value)):
            raise WorkspaceCliRunnerError("workspace CLI messages output is not an object array")
        return {"items": value}

    def execute(self, operation: str, *, group_id: str | None = None,
                session_id: str | None = None,
                file_id: str | None = None,
                session_title: str | None = None,
                message: str | None = None) -> dict:
        if operation in self.executed:
            raise WorkspaceCliRunnerError("workspace CLI operation was already executed")
        role, method, path, arguments = build_contract(
            self.command_context, operation, group_id, session_id,
            file_id, session_title, message,
        )
        credential_kind, credential = self._credentials()
        effects = {
            SESSION_FILE_CORE_PROFILE_CONTRACT: SESSION_FILE_CORE_EFFECT,
            SESSION_CLI_CREATE_GET_PROFILE_CONTRACT: SESSION_CLI_CREATE_GET_EFFECT,
            SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT: SESSION_CLI_LIFECYCLE_EFFECT,
            SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT: (
                SESSION_CLI_COMPLETE_INVOKE_EFFECT),
            SESSION_CLI_COLLECTION_PROFILE_CONTRACT: (
                "OWNED_SESSION_COLLECTION_THEN_GENERIC_GROUP_DELETE"),
            WORKSPACE_CLI_FUSE_PROFILE_CONTRACT: FUSE_EFFECT,
            WORKSPACE_CLI_STATUS_PROFILE_CONTRACT: STATUS_EFFECT,
            WORKSPACE_CLI_MEMBER_PROFILE_CONTRACT: WORKSPACE_CLI_MEMBER_EFFECT,
        }
        fixture_effect = effects.get(self.profile["contract"], CREATE_GET_EFFECT)
        root = context = environment = None
        primary: BaseException | None = None
        cleanup_status = "NOT_WRITTEN"
        started = time.monotonic()
        try:
            root, context, environment = self._context(credential_kind, credential)
            command = [str(self.binary), "--url", self.origin, "--json",
                       "--log-level", "error", *arguments]
            result = capture(
                command, cwd=root, environment=environment,
                timeout_seconds=self.profile["policy"]["timeout_seconds"],
                max_output_bytes=self.profile["policy"]["max_response_bytes"],
                collect_stderr=operation == "session-cli.service.wait",
            )
            if result.returncode != 0 and operation != "session-cli.service.wait":
                detail = result.stdout.strip().decode("utf-8", "replace")[:500]
                raise WorkspaceCliRunnerError(
                    "workspace product CLI returned nonzero: "
                    + (detail or "<no output>"))
            if (result.returncode != 0
                    and self._parse_service_wait_timeout_output(
                        result.stderr, session_id) is None):
                raise WorkspaceCliRunnerError(
                    "workspace bounded service wait exceeded its reviewed contract")
            raw = result.stdout
            if (credential.encode() in raw
                    or (result.returncode != 0
                        and credential.encode() in result.stderr)):
                raise WorkspaceCliRunnerError("workspace product CLI emitted its credential")
            if result.returncode != 0:
                data = self._parse_service_wait_timeout_output(
                    result.stderr, session_id)
            elif operation in GROUP_CREATE_OPERATIONS:
                data = self._parse_create_output(raw)
            elif operation == "fuse.group.fuse":
                data = self._parse_fuse_output(raw)
            elif operation == "member.group.add-member":
                data = self._parse_add_member_output(raw)
            elif operation == "session-file.delete":
                data = self._parse_delete_output(raw)
            elif operation == "session-cli.session.messages":
                data = self._parse_session_messages_output(raw)
            else:
                data = self._parse_json_output(raw)
        except ProcessCaptureError as error:
            public = WorkspaceCliRunnerError("workspace product CLI process failed")
            if getattr(error, "child_shutdown_status", None) == "UNCONFIRMED":
                public.child_shutdown_status = "UNCONFIRMED"
            primary = public
        except BaseException as error:
            primary = error
        finally:
            if context is not None and root is not None:
                cleanup_status = self._cleanup(context, root)
        if cleanup_status != "CLEARED":
            failure = WorkspaceCliRunnerError("workspace CLI private context cleanup is unconfirmed")
            failure.credential_cleanup_status = cleanup_status
            if primary is None:
                primary = failure
            else:
                primary.credential_cleanup_status = cleanup_status
        if primary is not None:
            if isinstance(primary, KeyboardInterrupt):
                raise primary
            if not isinstance(primary, WorkspaceCliRunnerError):
                primary = WorkspaceCliRunnerError("workspace product CLI output is invalid")
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
            "exit_status": result.returncode,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
            "fixture_effect": fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        }
        return {"data": data, "evidence": evidence}
