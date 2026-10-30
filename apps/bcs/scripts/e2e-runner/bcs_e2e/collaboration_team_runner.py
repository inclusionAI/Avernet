"""Bounded product-CLI runner for the complete collaboration-team Story."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid

from .bounded_process import ProcessCaptureError, capture, stderr_failure_detail
from .collaboration_team_http import (
    CollaborationTeamHttpError, read_formal_group_page,
)
from .collaboration_team_http import delete_group as delete_group_request
from .collaboration_team_profile import validate_collaboration_team_profile
from .local_cli import require_cli_binary
from .profile import identifier, text, unique_keys
from .run_state import write


class CollaborationTeamRunnerError(RuntimeError):
    pass


_GROUP_CONTEXT_OPERATIONS = frozenset({
    "group_context.workspace",
    "group_context.add.group",
    "group_context.add.session",
    "group_context.add.round",
    "group_context.get.initial",
    "group_context.update.group",
    "group_context.list.entries",
    "group_context.get.updated",
})
_DRIVER_TOKEN_OPERATIONS = (
    frozenset({"group.status", "group.terminate"}) | _GROUP_CONTEXT_OPERATIONS
)
_ROLE_OPERATIONS = {
    "visibility.protected": (
        "customer_service", "PUT",
        "/bots/{customer_service_id}/visibility",
        ("visibility", "set", "--value", "protected"),
    ),
    "visibility.public": (
        "customer_service", "PUT",
        "/bots/{customer_service_id}/visibility",
        ("visibility", "set", "--value", "public"),
    ),
    "friend.requests.before": (
        "customer_service", "GET",
        "/friends/requests?direction=received&status=pending",
        ("friend", "requests", "--direction", "received", "--status", "pending"),
    ),
    "friend.requests.select": (
        "customer_service", "GET",
        "/friends/requests?direction=received&status=pending",
        ("friend", "requests", "--direction", "received", "--status", "pending"),
    ),
    "friend.list": (
        "engineering", "GET",
        "/bots/{engineering_id}/friends",
        ("friend", "list",),
    ),
}


class CollaborationTeamRunner:
    """Execute only the reviewed collaboration-team CLI leaves, once each."""

    def __init__(self, profile: dict, *, binary, binary_sha256: str, state_dir):
        self.profile = validate_collaboration_team_profile(profile)
        if (not hasattr(state_dir, "is_absolute") or not state_dir.is_absolute()
                or state_dir.is_symlink() or not state_dir.is_dir()):
            raise CollaborationTeamRunnerError(
                "collaboration-team requires an absolute current Run directory")
        require_cli_binary(binary, binary_sha256)
        self.binary = binary.resolve()
        self.binary_sha256 = binary_sha256
        self.state_dir = state_dir
        self.origin = self.profile["entrypoint"]["origin"]
        self.ids = {role: self.profile["actors"][role]["bot_id"]
                    for role in ("engineering", "customer_service", "driver", "qa")}
        self.timeout = self.profile["policy"]["timeout_seconds"]
        self.max_response_bytes = self.profile["policy"]["max_response_bytes"]
        self.executed: list[str] = []
        self._formal_attempted = False
        self._current_group_id = ""
        self._group_context = {
            "group_id": "",
            "session_id": "",
            "round_id": "",
            "ctx_name": f"cli_e2e_context_{uuid.uuid4().hex[:12]}",
            "group_content": "initial group context from cli e2e",
            "group_updated": "updated group context from cli e2e",
            "session_content": "session group context from cli e2e",
            "round_content": "round group context from cli e2e",
        }


    @property
    def engineering_id(self) -> str:
        return self.ids["engineering"]

    @property
    def customer_service_id(self) -> str:
        return self.ids["customer_service"]

    @property
    def driver_id(self) -> str:
        return self.ids["driver"]

    @property
    def qa_id(self) -> str:
        return self.ids["qa"]

    def _credential(self, role: str, operation: str) -> tuple[str, str]:
        if self.profile["environment"]["tier"] == "local":
            value = os.environ.get(self.profile["auth"][f"{role}_token_env"], "")
            if not value:
                raise CollaborationTeamRunnerError(
                    f"owned {role} Bot credential is absent")
            return "owned_local_bot_token", value
        if operation in _DRIVER_TOKEN_OPERATIONS:
            value = os.environ.get(
                self.profile["auth"]["driver_token_env"], "")
            cookie = os.environ.get(self.profile["auth"]["cookie_env"], "")
            if not value or not cookie:
                raise CollaborationTeamRunnerError(
                    "owned driver Bot token and Human Cookie are absent")
            return (
                "human_cookie_with_owned_driver_bot_token", value,
            )
        value = os.environ.get(self.profile["auth"]["cookie_env"], "")
        if not value:
            raise CollaborationTeamRunnerError("Human Cookie is absent")
        return "human_cookie", value

    @staticmethod
    def _context(profile: dict, origin: str, role: str, credential_kind: str,
                 credential: str, state_dir):
        root = state_dir / ("collaboration-team-cli-" + uuid.uuid4().hex)
        root.mkdir(mode=0o700)
        for name in ("home", "bot", "tmp"):
            (root / name).mkdir(mode=0o700)
        context = root / "bot" / ".bcs" / "session.json"
        write(context, {
            "token": (
                credential
                if credential_kind in {
                    "owned_local_bot_token", "owned_driver_bot_token",
                    "human_cookie_with_owned_driver_bot_token",
                }
                else ""
            ),
            "bot_uuid": profile["actors"][role]["bot_id"],
            "api_base_url": origin,
        })
        base = str(root)
        environment = {
            "PATH": "/usr/bin:/bin", "HOME": base + "/home",
            "BOT_DATA_DIR": base + "/bot", "TMPDIR": base + "/tmp", "LANG": "C",
        }
        if credential_kind in {"human_cookie", "human_cookie_with_owned_driver_bot_token"}:
            cookie = (credential if credential_kind == "human_cookie"
                      else os.environ.get(profile["auth"]["cookie_env"], ""))
            if not cookie:
                raise CollaborationTeamRunnerError("Human Cookie is absent")
            environment["BCS_COOKIE"] = cookie
            # Select the reviewed non-Office OAuth transport. The Cookie still
            # carries the gateway user session; the Bot token remains the BCS
            # business principal for driver-only group governance operations.
            environment["tc_sdb_nenv"] = "production"
        return root, context, environment

    @staticmethod
    def _cleanup(context, root) -> str:
        status = "CLEARED"
        try:
            write(context, {"token": "", "bot_uuid": "", "api_base_url": ""})
            shutil.rmtree(root)
        except BaseException:
            status = "UNCONFIRMED"
        if root.exists():
            status = "UNCONFIRMED"
        return status

    def _command(self, operation: str, *, request_id: str = "", group_id: str = "",
                 topic: str = "") -> tuple[str, tuple[str, ...], str, str]:
        if operation in _ROLE_OPERATIONS:
            role, method, path, arguments = _ROLE_OPERATIONS[operation]
            path = path.format(engineering_id=self.engineering_id,
                               customer_service_id=self.customer_service_id)
            return role, arguments, method, path
        if operation == "friend.request":
            return ("engineering",
                    ("friend", "request", "--bot-uuid", self.customer_service_id),
                    "POST", "/friends/request")
        if operation == "friend.reject":
            identifier(request_id, "friend request ID")
            return ("customer_service",
                    ("friend", "reject", "--request-id", request_id),
                    "POST", f"/friends/requests/{request_id}/reject")
        if operation in ("group.create", "termination.group.create"):
            text(topic, "Group topic")
            return ("driver",
                    ("create-group", "--driver", self.driver_id,
                     "--participants", f"{self.driver_id},{self.engineering_id}",
                     "--context", "Release coordination through bcs-cli",
                     "--topic", topic),
                    "POST", "/groups")
        if operation in ("group.read.active", "group.read.after-member",
                         "group.read.completed", "termination.group.read.completed"):
            identifier(group_id, "Group ID")
            return ("driver", ("get-group", "--id", group_id),
                    "GET", f"/groups/{group_id}")
        if operation == "group.add-member":
            identifier(group_id, "Group ID")
            return ("driver",
                    ("add-member", "--group", group_id, "--bot-uuid", self.qa_id),
                    "POST", f"/groups/{group_id}/members")
        if operation == "group.list":
            return ("driver", ("list-groups", "--batch-size", "20"),
                    "GET", "/groups/my?offset=0&limit=20")
        if operation == "group.fuse":
            identifier(group_id, "Group ID")
            return ("driver",
                    ("fuse", "--group", group_id,
                     "--question", "Is the release ready?",
                     "--participants",
                     f"{self.driver_id},{self.engineering_id},{self.qa_id}",
                     "--focus", "release risk"),
                    "POST", f"/groups/{group_id}/fuse")
        if operation == "group.status":
            identifier(group_id, "Group ID")
            return ("driver",
                    ("group-status", "--group", group_id, "--status", "completed",
                     "--reason", "release reviewed"),
                    "PUT", f"/groups/{group_id}/status")
        if operation == "group.terminate":
            identifier(group_id, "Group ID")
            return ("driver", ("terminate-group", "--group", group_id),
                    "POST", f"/groups/{group_id}/terminate")
        if operation == "group_context.workspace":
            text(topic, "Group topic")
            return ("driver",
                    ("create-group", "--driver", self.driver_id,
                     "--participants", f"{self.driver_id},{self.engineering_id}",
                     "--context", "Release coordination through bcs-cli",
                     "--topic", topic),
                    "POST", "/groups")
        if operation == "group_context.add.group":
            return self._group_context_add_command("group", session_id=False)
        if operation == "group_context.add.session":
            return self._group_context_add_command("session", session_id=True)
        if operation == "group_context.add.round":
            return self._group_context_add_command("round", session_id=True, round_id=True)
        if operation == "group_context.get.initial":
            return ("driver", (
                "group-context", "get", "--group-id", self._group_context["group_id"],
                "--name", self._group_context["ctx_name"], "--scope-level", "group",
                "--limit", "3"), "POST", "/groupcontext/get")
        if operation == "group_context.update.group":
            return ("driver", (
                "group-context", "update", "--group-id", self._group_context["group_id"],
                "--name", self._group_context["ctx_name"], "--scope-level", "group",
                "--content", self._group_context["group_updated"],
                "--visible-to", json.dumps({"userIds": [self.driver_id]}, separators=(",", ":")),
                "--change-reason", "e2e update"), "POST", "/groupcontext/update")
        if operation == "group_context.list.entries":
            return ("driver", (
                "group-context", "list", "--group-id", self._group_context["group_id"],
                "--session-id", self._group_context["session_id"],
                "--round-id", self._group_context["round_id"],
                "--scope-levels", "group,session,round"), "POST", "/groupcontext/list")
        if operation == "group_context.get.updated":
            return ("driver", (
                "group-context", "get", "--group-id", self._group_context["group_id"],
                "--name", self._group_context["ctx_name"], "--scope-level", "group"),
                "POST", "/groupcontext/get")
        raise CollaborationTeamRunnerError(
            "unsupported collaboration-team CLI operation")

    def _group_context_add_command(self, scope_level: str, *, session_id: bool,
                                      round_id: bool = False):
        if not self._group_context["group_id"]:
            raise CollaborationTeamRunnerError("Group Context workspace is not ready")
        content_key = {"group": "group_content", "session": "session_content",
                       "round": "round_content"}[scope_level]
        visible_to = json.dumps({"userIds": [self.driver_id]}, separators=(",", ":"))
        arguments = ["group-context", "add"]
        if self._group_context["group_id"]:
            arguments += ["--group-id", self._group_context["group_id"]]
        if session_id or round_id:
            if not self._group_context["session_id"]:
                raise CollaborationTeamRunnerError("Group Context session is missing")
            arguments += ["--session-id", self._group_context["session_id"]]
        if round_id:
            if not self._group_context["round_id"]:
                raise CollaborationTeamRunnerError("Group Context round is missing")
            arguments += ["--round-id", self._group_context["round_id"]]
        arguments += ["--name", self._group_context["ctx_name"],
                      "--scope-level", scope_level,
                      "--content", self._group_context[content_key],
                      "--visible-to", visible_to,
                      "--collect-from", visible_to]
        return ("driver", tuple(arguments), "POST", "/groupcontext/add")

    def _after_group_context_workspace(self, data: dict) -> None:
        self._group_context["group_id"] = data["group_id"]
        self._group_context["session_id"] = data["session_id"]
        self._group_context["round_id"] = data["session_id"] + ":round-cli-e2e-" + uuid.uuid4().hex[:12]

    @staticmethod
    def _line(text: str, label: str) -> str:
        lines = re.findall(rf"^  {re.escape(label)}: (.*)$", text, re.M)
        if len(lines) != 1 or not lines[0].strip():
            raise CollaborationTeamRunnerError("CLI bounded line output is invalid")
        return lines[0].strip()

    def _parse_group_create(self, raw: bytes) -> dict:
        text = raw.decode("utf-8")
        if not text.startswith("Group created:\n"):
            raise CollaborationTeamRunnerError("Group create output prefix is invalid")
        group_id = identifier(self._line(text, "ID"), "collaboration-team Group ID")
        session_id = self._line(text, "Session")
        driver = self._line(text, "Driver")
        participants = [value.strip()
                        for value in self._line(text, "Participants").split(",")]
        if (not session_id.startswith(group_id + ":")
                or driver != self.driver_id
                or participants != [self.driver_id, self.engineering_id]):
            raise CollaborationTeamRunnerError("Group create output is not exact")
        return {"group_id": group_id, "driver_bot": driver,
                "participants": participants, "session_id": session_id}

    def _parse_add_member(self, raw: bytes) -> dict:
        text = raw.decode("utf-8")
        if not text.startswith("✓ Member added to group:\n"):
            raise CollaborationTeamRunnerError("add-member output prefix is invalid")
        group = self._line(text, "Group")
        bot = self._line(text, "Bot")
        if (group != self._current_group_id or bot != self.qa_id):
            raise CollaborationTeamRunnerError("add-member output is not exact")
        return {"group": group, "bot": bot}

    def _parse_fuse(self, raw: bytes) -> dict:
        text = raw.decode("utf-8")
        prefix = "Fusion result:\n"
        if not text.startswith(prefix):
            raise CollaborationTeamRunnerError("fusion output prefix is invalid")
        return self._parse_json(raw[len(prefix):])

    @staticmethod
    def _parse_json(raw: bytes):
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
        if not isinstance(value, (dict, list)):
            raise CollaborationTeamRunnerError("CLI JSON output is invalid")
        return value

    def execute(self, operation: str, *, request_id: str = "", group_id: str = "",
                topic: str = "") -> dict:
        if operation in self.executed:
            raise CollaborationTeamRunnerError(
                "collaboration-team operation was already executed")
        role, arguments, method, path = self._command(
            operation, request_id=request_id, group_id=group_id, topic=topic)
        if (operation in ("group.read.active", "group.read.after-member",
                         "group.read.completed",
                         "group.add-member", "group.fuse",
                         "group.status", "group.terminate",
                         "termination.group.read.completed")):
            self._current_group_id = identifier(group_id, "current Group ID")
        credential_kind, credential = self._credential(role, operation)
        root = context = environment = None
        primary: BaseException | None = None
        cleanup_status = "NOT_WRITTEN"
        started = time.monotonic()
        result = None
        try:
            root, context, environment = self._context(
                self.profile, self.origin, role, credential_kind, credential,
                self.state_dir)
            result = capture([str(self.binary), "--url", self.origin, "--json",
                              "--log-level", "error", *arguments],
                             cwd=root, environment=environment,
                             timeout_seconds=self.timeout,
                             max_output_bytes=self.max_response_bytes,
                             collect_stderr=True)
            if result.returncode != 0:
                raise CollaborationTeamRunnerError(
                    "collaboration-team product CLI returned nonzero: "
                    + (stderr_failure_detail(result, [credential.encode()])
                       or "<no stderr>"))
            if (credential.encode() in result.stdout
                    or credential.encode() in result.stderr):
                raise CollaborationTeamRunnerError(
                    "collaboration-team CLI emitted its credential")
            if operation in ("group.create", "termination.group.create",
                            "group_context.workspace"):
                data = self._parse_group_create(result.stdout)
                if operation == "group_context.workspace":
                    self._after_group_context_workspace(data)
            elif operation == "group.add-member":
                data = self._parse_add_member(result.stdout)
            elif operation == "group.fuse":
                data = self._parse_fuse(result.stdout)
            else:
                data = self._parse_json(result.stdout)
        except ProcessCaptureError as error:
            primary = CollaborationTeamRunnerError(
                "collaboration-team CLI process failed")
            if getattr(error, "child_shutdown_status", None) == "UNCONFIRMED":
                primary.child_shutdown_status = "UNCONFIRMED"
        except BaseException as error:
            primary = error
        finally:
            if context is not None and root is not None:
                cleanup_status = self._cleanup(context, root)
        if cleanup_status != "CLEARED":
            failure = CollaborationTeamRunnerError(
                "CLI private context cleanup is unconfirmed")
            failure.credential_cleanup_status = cleanup_status
            primary = failure if primary is None else primary
        if primary is not None:
            if isinstance(primary, KeyboardInterrupt):
                raise primary
            if not isinstance(primary, CollaborationTeamRunnerError):
                primary = CollaborationTeamRunnerError(
                    "collaboration-team CLI output is invalid")
            raise primary
        self.executed.append(operation)
        return {
            "data": data,
            "evidence": {
                "transport": "PRODUCT_CLI", "operation": operation,
                "caller_role": role, "command_arguments": list(arguments),
                "http_method_declared": method, "route_declared": path,
                "credential_kind": credential_kind,
                "exit_status": result.returncode,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "remote_validation": "NOT_RUN", "grants_release_credit": False,
            },
        }

    def read_formal_group_page(self, expected_group_id: str) -> dict:
        identifier(expected_group_id, "formal Group ID")
        if self._formal_attempted:
            raise CollaborationTeamRunnerError("formal group page read is one-shot")
        self._formal_attempted = True
        try:
            return read_formal_group_page(self.profile, expected_group_id)
        except CollaborationTeamHttpError as error:
            raise CollaborationTeamRunnerError(str(error)) from None

    def delete_group(self, group_id: str) -> dict:
        identifier(group_id, "Group Context group ID")
        if self._group_context["group_id"] != group_id:
            raise CollaborationTeamRunnerError("Group Context cleanup group differs")
        try:
            return delete_group_request(self.profile, group_id)
        except CollaborationTeamHttpError as error:
            raise CollaborationTeamRunnerError(str(error)) from None
