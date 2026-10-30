"""Bounded product-CLI runner for the Channel management slice."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from .bounded_process import ProcessCaptureError, capture
from .channel_cli_profile import (
    CHANNEL_CLI_BIND_OPERATION,
    CHANNEL_CLI_CONVERSATION_EMPTY_OPERATION,
    CHANNEL_CLI_GROUP_CREATE_OPERATION,
    CHANNEL_CLI_LIST_AFTER_OPERATION,
    CHANNEL_CLI_LIST_BEFORE_OPERATION,
    CHANNEL_CLI_SESSION_CREATE_OPERATION,
    CHANNEL_CLI_UNBIND_MISSING_OPERATION,
    CHANNEL_CLI_CLI_OPERATIONS,
    validate_channel_cli_profile,
)
from .local_cli import require_cli_binary
from .profile import identifier, unique_keys
from .run_state import write


class ChannelCliRunnerError(RuntimeError):
    pass


DISABLED_BRIDGE_MARKER = "channel bridge is disabled"
CREATE_GROUP_TOPIC = "CLI channel conversation lookup"
CREATE_GROUP_CONTEXT = "Release coordination through bcs-cli"
LOOKUP_SESSION_TITLE = "CLI channel lookup"

EFFECT = "OWNED_CHANNEL_CLI_LIFECYCLE_THEN_EXACT_SESSION_GROUP_DELETE"


class ChannelCliRunner:
    """Execute only the reviewed Channel parent CLI leaves once, in order."""

    def __init__(self, profile: dict, *, binary: Path, binary_sha256: str,
                 state_dir: Path):
        self.profile = validate_channel_cli_profile(profile)
        if (not isinstance(state_dir, Path) or not state_dir.is_absolute()
                or state_dir.is_symlink() or not state_dir.is_dir()):
            raise ChannelCliRunnerError(
                "channel CLI requires an absolute current Run directory")
        require_cli_binary(binary, binary_sha256)
        self.binary = Path(binary).resolve()
        self.binary_sha256 = binary_sha256
        self.state_dir = state_dir
        self.origin = self.profile["entrypoint"]["origin"]
        self.tier = self.profile["environment"]["tier"]
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.eng_id = self.profile["actors"]["eng"]["bot_id"]
        self.roles = {"driver": self.driver_id, "member": self.member_id,
                      "eng": self.eng_id}
        self.account = "cli-channel-" + uuid.uuid4().hex[:16]
        self.missing = ("missing-channel-cli-"
                        + hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest()[:24])
        self.group_id: str | None = None
        self.initial_session_id: str | None = None
        self.session_id: str | None = None
        self.executed: list[str] = []

    def _credentials(self, role: str) -> tuple[str, str]:
        if self.tier == "local":
            value = os.environ.get(self.profile["auth"][f"{role}_token_env"], "")
            if not value:
                raise ChannelCliRunnerError(
                    f"channel CLI owned {role} Bot credential is absent")
            return "owned_local_bot_token", value
        value = os.environ.get(self.profile["auth"]["cookie_env"], "")
        if not value:
            raise ChannelCliRunnerError("channel CLI Human Cookie is absent")
        return "human_cookie", value

    def _env_flag(self) -> str:
        return "local" if self.tier == "local" else "pre"

    def _contract(self, operation: str, *, group_id: str | None = None,
                  session_id: str | None = None, secret: str = "") -> tuple:
        if operation == CHANNEL_CLI_BIND_OPERATION:
            if (not isinstance(secret, str) or not secret.startswith("synthetic-")
                    or not secret.endswith("-channel-secret") or len(secret) != 57):
                raise ChannelCliRunnerError(
                    "channel bind requires one fresh in-memory synthetic secret")
            return ("driver", "POST", "/channels/bindings", (
                "channel", "bind", "--account", self.account,
                "--target-kind", "bot", "--target-id", self.driver_id,
                "--visibility", "full_transcript", "--env", self._env_flag(),
                "--robot-code", "e2e-robot", "--client-id", "e2e-client",
                "--client-secret", secret, "--send-mode", "normal",
                "--message-type", "markdown",
            ))
        if operation in (CHANNEL_CLI_LIST_BEFORE_OPERATION,
                         CHANNEL_CLI_LIST_AFTER_OPERATION):
            return ("driver", "GET", "/channels/bindings", ("channel", "list"))
        if operation == CHANNEL_CLI_GROUP_CREATE_OPERATION:
            return ("member", "POST", "/groups", (
                "create-group", "--driver", self.member_id,
                "--participants", f"{self.member_id},{self.eng_id}",
                "--context", CREATE_GROUP_CONTEXT, "--topic", CREATE_GROUP_TOPIC,
            ))
        if operation == CHANNEL_CLI_SESSION_CREATE_OPERATION:
            gid = self._require_bound_group(group_id)
            return ("member", "POST", f"/groups/{gid}/sessions", (
                "session", "create", "--group", gid,
                "--title", LOOKUP_SESSION_TITLE,
            ))
        if operation == CHANNEL_CLI_CONVERSATION_EMPTY_OPERATION:
            sid = self._require_bound_session(session_id)
            path = ("/channels/conversations/by-session?bcs_session_id="
                    + sid + "&channel_type=dingtalk")
            return ("member", "GET", path, (
                "channel", "conversation-id", "--session", sid,
            ))
        if operation == CHANNEL_CLI_UNBIND_MISSING_OPERATION:
            return ("driver", "DELETE", f"/channels/bindings/{self.missing}", (
                "channel", "unbind", "--id", self.missing,
            ))
        raise ChannelCliRunnerError("unsupported channel CLI operation")

    def _require_bound_group(self, group_id: object) -> str:
        if not isinstance(group_id, str) or not group_id:
            raise ChannelCliRunnerError(
                "channel CLI operation requires the exact Group ID")
        return group_id

    def _require_bound_session(self, session_id: object) -> str:
        if not isinstance(session_id, str) or not session_id:
            raise ChannelCliRunnerError(
                "channel CLI operation requires the exact Session ID")
        return session_id

    def _context(self, role: str, credential_kind: str, credential: str):
        root = self.state_dir / f"channel-cli-{uuid.uuid4().hex}"
        root.mkdir(mode=0o700)
        for name in ("home", "bot", "tmp"):
            (root / name).mkdir(mode=0o700)
        context = root / "bot" / ".bcs" / "session.json"
        data = {
            "token": credential if credential_kind == "owned_local_bot_token" else "",
            "bot_uuid": self.roles[role],
            "api_base_url": self.origin,
        }
        write(context, data)
        base = str(root)
        environment = {"PATH": "/usr/bin:/bin", "HOME": base + "/home",
                       "BOT_DATA_DIR": base + "/bot", "TMPDIR": base + "/tmp",
                       "LANG": "C"}
        if credential_kind == "human_cookie":
            environment["BCS_COOKIE"] = credential
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

    @staticmethod
    def _load_json_output(raw: bytes) -> dict:
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
        except (UnicodeError, json.JSONDecodeError):
            raise ChannelCliRunnerError(
                "channel CLI did not emit one JSON value") from None
        if not isinstance(value, dict):
            raise ChannelCliRunnerError("channel CLI JSON output is not an object")
        return value

    @staticmethod
    def _line(text: str, label: str) -> str:
        lines = re.findall(rf"^  {re.escape(label)}: (.*)$", text, re.M)
        if len(lines) != 1 or not lines[0].strip():
            raise ChannelCliRunnerError(
                "channel CLI create output misses its bounded line contract")
        return lines[0].strip()

    def _parse_bind_output(self, result, secret: str) -> dict:
        if result.returncode == 0:
            raise ChannelCliRunnerError(
                "channel bind under the disabled bridge must fail nonzero")
        try:
            stdout_text = result.stdout.decode("utf-8")
            stderr_text = result.stderr.decode("utf-8")
        except UnicodeError:
            raise ChannelCliRunnerError(
                "channel bind output was not UTF-8") from None
        if secret in stdout_text or secret in stderr_text:
            raise ChannelCliRunnerError(
                "channel bind leaked the synthetic client secret")
        if DISABLED_BRIDGE_MARKER not in stdout_text + stderr_text:
            raise ChannelCliRunnerError(
                "channel bind lacks the disabled-bridge diagnostic")
        return {"disabled_bridge": True, "secret_redacted": True}

    def _parse_list_output(self, raw: bytes) -> dict:
        value = self._load_json_output(raw)
        items = value.get("items")
        if not isinstance(items, list):
            raise ChannelCliRunnerError(
                "channel list envelope differs from the product contract")
        for item in items:
            if (isinstance(item, dict)
                    and item.get("account_ref") == self.account):
                raise ChannelCliRunnerError(
                    "channel list shows a phantom binding for the case account")
        return {"items": items}

    def _parse_conversation_output(self, raw: bytes) -> dict:
        value = self._load_json_output(raw)
        items = value.get("items")
        if items != []:
            raise ChannelCliRunnerError(
                "channel conversation lookup is not empty for the exact Session")
        return {"items": []}

    def _parse_unbind_output(self, raw: bytes) -> dict:
        value = self._load_json_output(raw)
        if value.get("ok") is not True:
            raise ChannelCliRunnerError(
                "missing channel unbind acknowledgement differs")
        return {"ok": True, "binding_id": self.missing}

    def _parse_session_output(self, raw: bytes, group_id: str) -> dict:
        value = self._load_json_output(raw)
        session_id = identifier(value.get("session_id"),
                                "channel CLI explicit Session ID")
        if (value.get("id") != session_id
                or value.get("group_id") != group_id
                or session_id == self.initial_session_id):
            raise ChannelCliRunnerError(
                "channel CLI explicit Session differs from its Group contract")
        return {"session_id": session_id, "id": session_id, "group_id": group_id}

    def _parse_group_output(self, raw: bytes) -> dict:
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise ChannelCliRunnerError(
                "channel CLI create-group output was not UTF-8") from None
        if not text.startswith("Group created:"):
            raise ChannelCliRunnerError(
                "channel CLI create-group output prefix is invalid")
        group_id = identifier(self._line(text, "ID"), "channel CLI Group ID")
        initial_session_id = identifier(self._line(text, "Session"),
                                        "channel CLI implicit Session ID")
        if not initial_session_id.startswith(group_id + ":"):
            raise ChannelCliRunnerError(
                "channel CLI implicit Session is not scoped to the exact Group")
        driver = self._line(text, "Driver")
        participants = self._line(text, "Participants").split(",")
        if (driver != self.member_id
                or [value.strip() for value in participants]
                != [self.member_id, self.eng_id]):
            raise ChannelCliRunnerError(
                "channel CLI create-group driver or participants differ")
        return {
            "group_id": group_id,
            "session_id": initial_session_id,
            "driver_bot": driver,
            "participants": [value.strip() for value in participants],
            "context": CREATE_GROUP_CONTEXT,
            "topic": CREATE_GROUP_TOPIC,
        }

    def execute(self, operation: str, *, group_id: str | None = None,
                session_id: str | None = None) -> dict:
        if operation not in CHANNEL_CLI_CLI_OPERATIONS:
            raise ChannelCliRunnerError("operation is not a reviewed channel CLI leaf")
        if operation in self.executed:
            raise ChannelCliRunnerError("channel CLI operation was already executed")
        expected_index = CHANNEL_CLI_CLI_OPERATIONS.index(operation)
        if self.executed != list(CHANNEL_CLI_CLI_OPERATIONS[:expected_index]):
            raise ChannelCliRunnerError(
                "channel CLI operations cannot be reordered or skipped")
        secret = ""
        if operation == CHANNEL_CLI_BIND_OPERATION:
            secret = "synthetic-" + uuid.uuid4().hex + "-channel-secret"
        role, method, path, arguments = self._contract(
            operation, group_id=group_id, session_id=session_id, secret=secret)
        credential_kind, credential = self._credentials(role)
        root = context = environment = None
        primary: BaseException | None = None
        cleanup_status = "NOT_WRITTEN"
        started = time.monotonic()
        data = None
        try:
            root, context, environment = self._context(
                role, credential_kind, credential)
            command = [str(self.binary), "--url", self.origin, "--json",
                       "--log-level", "error", *arguments]
            result = capture(
                command, cwd=root, environment=environment,
                timeout_seconds=self.profile["policy"]["timeout_seconds"],
                max_output_bytes=self.profile["policy"]["max_response_bytes"],
            )
            raw = result.stdout
            if (credential.encode() in raw
                    or credential.encode() in result.stderr):
                raise ChannelCliRunnerError(
                    "channel product CLI emitted its credential")
            if operation == CHANNEL_CLI_BIND_OPERATION:
                data = self._parse_bind_output(result, secret)
            elif operation == CHANNEL_CLI_GROUP_CREATE_OPERATION:
                data = self._parse_group_output(raw)
                self.group_id = data["group_id"]
                self.initial_session_id = data["session_id"]
            elif operation == CHANNEL_CLI_SESSION_CREATE_OPERATION:
                data = self._parse_session_output(raw, self._require_bound_group(group_id))
                self.session_id = data["session_id"]
            elif operation == CHANNEL_CLI_CONVERSATION_EMPTY_OPERATION:
                data = self._parse_conversation_output(raw)
            elif operation == CHANNEL_CLI_UNBIND_MISSING_OPERATION:
                data = self._parse_unbind_output(raw)
            else:
                data = self._parse_list_output(raw)
        except ProcessCaptureError as error:
            public = ChannelCliRunnerError("channel product CLI process failed")
            if getattr(error, "child_shutdown_status", None) == "UNCONFIRMED":
                public.child_shutdown_status = "UNCONFIRMED"
            primary = public
        except BaseException as error:
            primary = error
        finally:
            if context is not None and root is not None:
                cleanup_status = self._cleanup(context, root)
        secret = ""
        if cleanup_status != "CLEARED":
            failure = ChannelCliRunnerError(
                "channel CLI private context cleanup is unconfirmed")
            failure.credential_cleanup_status = cleanup_status
            if primary is None:
                primary = failure
            else:
                primary.credential_cleanup_status = cleanup_status
        if primary is not None:
            if isinstance(primary, KeyboardInterrupt):
                raise primary
            if not isinstance(primary, ChannelCliRunnerError):
                primary = ChannelCliRunnerError(
                    "channel product CLI output is invalid")
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
            "fixture_effect": EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        }
        return {"data": data, "evidence": evidence}
