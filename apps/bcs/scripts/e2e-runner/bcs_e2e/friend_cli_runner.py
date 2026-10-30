"""Bounded product-CLI executor for the dual friendship acceptance slice."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time
import uuid

from .bounded_process import capture, ProcessCaptureError, stderr_failure_detail
from .friend_cli_profile import validate_friend_cli_profile
from .local_cli import require_cli_binary
from .profile import unique_keys
from .run_state import write


class FriendCliRunnerError(RuntimeError):
    pass


OPERATIONS = {
    "friend.requests.before": (
        "member", "GET", "/friends/requests?direction=received&status=pending",
        ("friend", "requests", "--direction", "received", "--status", "pending"),
    ),
    "friend.request": None,
    "friend.requests.select": (
        "member", "GET", "/friends/requests?direction=received&status=pending",
        ("friend", "requests", "--direction", "received", "--status", "pending"),
    ),
    "friend.accept": None,
    "friend.reject": None,
    "friend.list": None,
    "member.visibility.protected": (
        "member", "PUT", "/bots/{member_id}/visibility",
        ("visibility", "set", "--value", "protected"),
    ),
    "member.visibility.public": (
        "member", "PUT", "/bots/{member_id}/visibility",
        ("visibility", "set", "--value", "public"),
    ),
}


class FriendCliRunner:
    """Execute only the reviewed friend product-CLI commands once, in order."""

    def __init__(self, profile: dict, *, binary: Path, binary_sha256: str, state_dir: Path):
        self.profile = validate_friend_cli_profile(profile)
        if (not isinstance(state_dir, Path) or not state_dir.is_absolute()
                or state_dir.is_symlink() or not state_dir.is_dir()):
            raise FriendCliRunnerError("friendship CLI requires an absolute current Run directory")
        require_cli_binary(binary, binary_sha256)
        self.binary = Path(binary).resolve()
        self.binary_sha256 = binary_sha256
        self.state_dir = state_dir
        self.origin = self.profile["entrypoint"]["origin"]
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.executed: list[str] = []

    def _credentials(self, role: str) -> tuple[str, str]:
        if self.profile["environment"]["tier"] == "local":
            key = ("driver_token_env" if role == "driver" else "member_token_env")
            value = os.environ.get(self.profile["auth"][key], "")
            if not value:
                raise FriendCliRunnerError("friendship CLI owned Bot credential is absent")
            return "owned_local_bot_token", value
        value = os.environ.get(self.profile["auth"]["cookie_env"], "")
        if not value:
            raise FriendCliRunnerError("friendship CLI Human Cookie is absent")
        return "human_cookie", value

    def _contract(self, operation: str, request_id: str | None):
        if operation == "friend.request":
            return (
                "driver", "POST", "/friends/request",
                ("friend", "request", "--bot-uuid", self.member_id),
            )
        if operation == "friend.accept":
            if not isinstance(request_id, str) or not request_id:
                raise FriendCliRunnerError("friend.accept requires the exact request ID")
            return (
                "member", "POST", f"/friends/requests/{request_id}/accept",
                ("friend", "accept", "--request-id", request_id),
            )
        if operation == "friend.reject":
            if not isinstance(request_id, str) or not request_id:
                raise FriendCliRunnerError("friend.reject requires the exact request ID")
            return (
                "member", "POST", f"/friends/requests/{request_id}/reject",
                ("friend", "reject", "--request-id", request_id),
            )
        if operation == "friend.list":
            return (
                "driver", "GET", f"/bots/{self.driver_id}/friends",
                ("friend", "list",),
            )
        row = OPERATIONS.get(operation)
        if row is None:
            raise FriendCliRunnerError("unsupported friendship CLI operation")
        role, method, path, arguments = row
        # Visibility targets the same dedicated member admitted by the Profile.
        if "{member_id}" in path:
            path = path.replace("{member_id}", self.member_id)
        return role, method, path, arguments

    def _context(self, role: str, credential_kind: str, credential: str):
        root = self.state_dir / f"friend-cli-{uuid.uuid4().hex}"
        root.mkdir(mode=0o700)
        for name in ("home", "bot", "tmp"):
            (root / name).mkdir(mode=0o700)
        context = root / "bot" / ".bcs" / "session.json"
        data = {
            "token": credential if credential_kind == "owned_local_bot_token" else "",
            "bot_uuid": self.profile["actors"][role]["bot_id"],
            "api_base_url": self.origin,
        }
        write(context, data)
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(root / "home"),
            "BOT_DATA_DIR": str(root / "bot"),
            "TMPDIR": str(root / "tmp"),
            "LANG": "C",
        }
        if credential_kind == "human_cookie":
            environment["BCS_COOKIE"] = credential
            # Select the reviewed non-Office OAuth transport. The Cookie still
            # carries the gateway user session; the product CLI must not probe
            # the remote BCS URL and fall into the Office OAuth path.
            environment["tc_sdb_nenv"] = "production"
        return root, context, environment

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

    def execute(self, operation: str, *, request_id: str | None = None) -> dict:
        if operation in self.executed:
            raise FriendCliRunnerError("friendship CLI operation was already executed")
        role, method, path, arguments = self._contract(operation, request_id)
        credential_kind, credential = self._credentials(role)
        root = context = environment = None
        primary: BaseException | None = None
        cleanup_status = "NOT_WRITTEN"
        started = time.monotonic()
        try:
            root, context, environment = self._context(role, credential_kind, credential)
            command = [str(self.binary), "--url", self.origin, "--json",
                       "--log-level", "error", *arguments]
            result = capture(
                command, cwd=root, environment=environment,
                timeout_seconds=self.profile["policy"]["timeout_seconds"],
                max_output_bytes=self.profile["policy"]["max_response_bytes"],
                collect_stderr=True,
            )
            if result.returncode != 0:
                raise FriendCliRunnerError(
                    "friendship product CLI returned nonzero: "
                    + (stderr_failure_detail(result, [credential.encode()])
                       or "<no stderr>"))
            raw = result.stdout
            if credential.encode() in raw:
                raise FriendCliRunnerError("friendship product CLI emitted its credential")
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
        except ProcessCaptureError as error:
            public = FriendCliRunnerError("friendship product CLI process failed")
            if getattr(error, "child_shutdown_status", None) == "UNCONFIRMED":
                public.child_shutdown_status = "UNCONFIRMED"
            primary = public
        except BaseException as error:
            primary = error
        finally:
            if context is not None and root is not None:
                cleanup_status = self._cleanup(context, root)
        if cleanup_status != "CLEARED":
            failure = FriendCliRunnerError("friendship CLI private context cleanup is unconfirmed")
            failure.credential_cleanup_status = cleanup_status
            if primary is None:
                primary = failure
            else:
                primary.credential_cleanup_status = cleanup_status
        if primary is not None:
            if isinstance(primary, KeyboardInterrupt):
                raise primary
            if not isinstance(primary, FriendCliRunnerError):
                primary = FriendCliRunnerError("friendship product CLI response is invalid")
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
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        }
        return {"data": data, "evidence": evidence}
