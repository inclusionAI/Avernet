"""Bounded product-CLI executor for the operator parent Story.

The runner replays the thirteen reviewed obligations of
``story_operator_coordinates_with_cli`` as twenty ordered, once-only
product-CLI operations against one dual-environment Profile: unauthenticated
health, the complete five-bot trusted-team listing, targeted reads, name
discovery, the visibility round-trip, session connect, the non-mutating web
onboard, the direct onboard leg (its HTTP register legs stay with the
adapter), the status broadcast round-trip, the creator PM/ENG Group with its
get-group readback, the detached chat handle, the paginated group listing
and the friend listing after the two in-case friendship admissions.

The obligation map is frozen by ``local_operator_cli_contract`` and the
reviewed ``operator-cli-migration-contract.json``; every execution carries a
private context whose session state is zeroed and whose directory tree is
removed before the result is returned, and no credential or token-bearing
output is ever journaled into evidence. Station faces may echo the
submitted credential inside a successful live response (connect returns
its bot token); such output is scrubbed in memory before any projection.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from .bounded_process import ProcessCaptureError, capture, stderr_failure_detail
from .local_cli import require_cli_binary
from .profile import ConfigError, identifier, origin, unique_keys
from .run_state import write
from .operator_cli_profile import (
    OPERATOR_CLI_ACTOR_ROLES,
    OPERATOR_CLI_VISIBILITY_LEVELS,
    validate_operator_cli_profile,
)
from .operator_cli_profile import (  # noqa: F401 -- frozen review tables re-exported
    OPERATOR_CLI_PROFILE_CONTRACT,
    OPERATOR_CLI_RELATIONSHIPS,
    OPERATOR_CLI_ROUTE_BINDINGS,
    OPERATOR_CLI_STATION_BINDINGS,
)

OPERATOR_CLI_EFFECT = (
    "CREATOR_SCOPED_GROUP_DELETE_TEMP_BOT_EXACT_LIFECYCLE_"
    "TWO_RETAINED_FRIENDSHIPS")

OPERATOR_CLI_HEALTH = "operator_cli.health"
OPERATOR_CLI_LIST = "operator_cli.list"
OPERATOR_CLI_GET = "operator_cli.get"
OPERATOR_CLI_DISCOVER = "operator_cli.discover"
OPERATOR_CLI_VISIBILITY = "operator_cli.visibility"
OPERATOR_CLI_CONNECT = "operator_cli.connect"
OPERATOR_CLI_ONBOARD_WEB = "operator_cli.onboard_web"
OPERATOR_CLI_LIST_AFTER_WEB_ONBOARD = "operator_cli.list_after_web_onboard"
OPERATOR_CLI_ONBOARD_DIRECT = "operator_cli.onboard_direct"
OPERATOR_CLI_ONBOARD_READBACK = "operator_cli.onboard_readback"
OPERATOR_CLI_UPDATE_STATUS = "operator_cli.update_status"
OPERATOR_CLI_CREATE_GROUP = "operator_cli.create_group"
OPERATOR_CLI_GET_GROUP = "operator_cli.get_group"
OPERATOR_CLI_FRIEND_REQUEST_CHAT = "operator_cli.friend_request_chat"
OPERATOR_CLI_FRIEND_ACCEPT_CHAT = "operator_cli.friend_accept_chat"
OPERATOR_CLI_CHAT = "operator_cli.chat"
OPERATOR_CLI_LIST_GROUPS = "operator_cli.list_groups"
OPERATOR_CLI_FRIEND_REQUEST_LIST = "operator_cli.friend_request_list"
OPERATOR_CLI_FRIEND_ACCEPT_LIST = "operator_cli.friend_accept_list"
OPERATOR_CLI_FRIEND_LIST = "operator_cli.friend_list"

OPERATOR_CLI_CLI_OPERATIONS = (
    OPERATOR_CLI_HEALTH,
    OPERATOR_CLI_LIST,
    OPERATOR_CLI_GET,
    OPERATOR_CLI_DISCOVER,
    OPERATOR_CLI_VISIBILITY,
    OPERATOR_CLI_CONNECT,
    OPERATOR_CLI_ONBOARD_WEB,
    OPERATOR_CLI_LIST_AFTER_WEB_ONBOARD,
    OPERATOR_CLI_ONBOARD_DIRECT,
    OPERATOR_CLI_ONBOARD_READBACK,
    OPERATOR_CLI_UPDATE_STATUS,
    OPERATOR_CLI_CREATE_GROUP,
    OPERATOR_CLI_GET_GROUP,
    OPERATOR_CLI_FRIEND_REQUEST_CHAT,
    OPERATOR_CLI_FRIEND_ACCEPT_CHAT,
    OPERATOR_CLI_CHAT,
    OPERATOR_CLI_LIST_GROUPS,
    OPERATOR_CLI_FRIEND_REQUEST_LIST,
    OPERATOR_CLI_FRIEND_ACCEPT_LIST,
    OPERATOR_CLI_FRIEND_LIST,
)

# Declared per-operation transport contract for evidence projection only.
# These strings describe the reviewed legacy product routes; the runner
# itself never opens a socket.
OPERATOR_CLI_ROUTES = {
    OPERATOR_CLI_HEALTH: ("GET", "/health"),
    OPERATOR_CLI_LIST: ("GET", "/bots"),
    OPERATOR_CLI_GET: ("GET", "/bots/{bot_uuid}"),
    OPERATOR_CLI_DISCOVER: ("GET", "/bots/discover?query={query}"),
    OPERATOR_CLI_VISIBILITY: ("GET/PUT", "/bots/{driver_actor_id}/visibility"),
    OPERATOR_CLI_CONNECT: ("POST", "/bots/{driver_actor_id}/connect"),
    OPERATOR_CLI_ONBOARD_WEB: ("GET", "/register?web=1"),
    OPERATOR_CLI_LIST_AFTER_WEB_ONBOARD: ("GET", "/bots"),
    OPERATOR_CLI_ONBOARD_DIRECT: ("POST", "/register?token={register_token}&bot-name={bot_name}"),
    OPERATOR_CLI_ONBOARD_READBACK: ("GET", "/bots/{bot_uuid}"),
    OPERATOR_CLI_UPDATE_STATUS: ("POST", "/bots/{driver_actor_id}/state"),
    OPERATOR_CLI_CREATE_GROUP: ("POST", "/groups"),
    OPERATOR_CLI_GET_GROUP: ("GET", "/groups/{group_id}"),
    OPERATOR_CLI_FRIEND_REQUEST_CHAT: ("POST", "/friends/request"),
    OPERATOR_CLI_FRIEND_ACCEPT_CHAT: ("POST", "/friends/requests/{request_id}/accept"),
    OPERATOR_CLI_CHAT: ("POST", "/bots/{member_actor_id}/chat"),
    OPERATOR_CLI_LIST_GROUPS: ("GET", "/groups"),
    OPERATOR_CLI_FRIEND_REQUEST_LIST: ("POST", "/friends/request"),
    OPERATOR_CLI_FRIEND_ACCEPT_LIST: ("POST", "/friends/requests/{request_id}/accept"),
    OPERATOR_CLI_FRIEND_LIST: ("GET", "/bots/{driver_actor_id}/friends"),
}


CREATE_GROUP_TOPIC = "operator cli e2e group"
WEB_ONBOARD_SUMMARY = "operator e2e throwaway probe"
DIRECT_ONBOARD_SUMMARY = "Operator CLI direct onboarding"
DIRECT_SKILLS = "triage,review"
DIRECT_DOMAINS = "release"
DIRECT_SCOPES = "local"
CHAT_MESSAGE = "operator e2e chat"

VISIBILITY_LINE = re.compile(r"(?m)^Visibility: (public|protected|private)$")
GROUP_CREATE_ID = re.compile(r"(?m)^  ID: ([A-Za-z0-9_-]+)$")


class OperatorCliRunnerError(RuntimeError):
    pass


class OperatorCliRunner:
    """Execute the twenty reviewed operator CLI operations once, in order.

    Friend-admission legs honor a stateful station's idempotent
    already-friends answer: when the fixture pair is already admitted
    station-side (the preprod revoke asymmetry makes those pairs permanent,
    disclosed separately), the request leg records ALREADY_ADMITTED backed
    by one bounded live membership read instead of fabricating a pending
    request ID, and the paired accept leg records its substitution without
    invoking the accept route. Every other shape stays fail-closed.
    """

    def __init__(self, profile: dict, *, binary: Path, binary_sha256: str,
                 state_dir: Path):
        self.profile = validate_operator_cli_profile(profile)
        if (not isinstance(state_dir, Path) or not state_dir.is_absolute()
                or state_dir.is_symlink() or not state_dir.is_dir()):
            raise OperatorCliRunnerError(
                "operator CLI requires an absolute current Run directory")
        require_cli_binary(binary, binary_sha256)
        self.binary = Path(binary).resolve()
        self.binary_sha256 = binary_sha256
        self.state_dir = state_dir
        self.origin = self.profile["entrypoint"]["origin"]
        self.tier = self.profile["environment"]["tier"]
        self.actor_ids = {role: self.profile["actors"][role]["bot_id"]
                          for role in OPERATOR_CLI_ACTOR_ROLES}
        self.fixture_names = dict(self.profile["fixture_names"])
        self.group_id: str | None = None
        self.web_probe: str | None = None
        self.chat_request_id: str | None = None
        self.list_request_id: str | None = None
        self.chat_admission: str | None = None
        self.list_admission: str | None = None
        self.executed: list[str] = []

    def _credentials(self, role: str, *, onboard_token: str = "") -> tuple[str, str, str]:
        """Return (credential_kind, session token, cookie) for one role."""
        if role == "health":
            return "none", "", ""
        if role == "onboard":
            if not isinstance(onboard_token, str) or not onboard_token:
                raise OperatorCliRunnerError(
                    "operator CLI direct onboard requires the registered Bot token")
            if self.tier == "local":
                return "owned_local_bot_token", onboard_token, ""
            cookie = os.environ.get(self.profile["auth"]["cookie_env"], "")
            if not cookie:
                raise OperatorCliRunnerError("operator CLI Human Cookie is absent")
            return "human_cookie_with_owned_onboarded_bot_token", onboard_token, cookie
        if self.tier == "local":
            value = os.environ.get(self.profile["auth"][f"{role}_token_env"], "")
            if not value:
                raise OperatorCliRunnerError(
                    f"operator CLI owned {role} Bot credential is absent")
            return "owned_local_bot_token", value, ""
        cookie = os.environ.get(self.profile["auth"]["cookie_env"], "")
        if not cookie:
            raise OperatorCliRunnerError("operator CLI Human Cookie is absent")
        if role == "driver":
            token = os.environ.get(self.profile["auth"]["driver_token_env"], "")
            if not token:
                raise OperatorCliRunnerError("operator CLI driver Bot token is absent")
            return "human_cookie_with_owned_driver_bot_token", token, cookie
        return "human_cookie", "", cookie

    def _context(self, role: str, credential_kind: str, token: str, cookie: str):
        root = self.state_dir / f"operator-cli-{uuid.uuid4().hex}"
        root.mkdir(mode=0o700)
        for name in ("home", "bot", "tmp"):
            (root / name).mkdir(mode=0o700)
        context = root / "bot" / ".bcs" / "session.json"
        bot_uuid = "" if role in {"health", "onboard"} else self.actor_ids[role]
        session_token = token if credential_kind in {
            "owned_local_bot_token", "human_cookie_with_owned_driver_bot_token",
            "human_cookie_with_owned_onboarded_bot_token"} else ""
        data = {"token": session_token, "bot_uuid": bot_uuid,
                "api_base_url": self.origin}
        write(context, data)
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(root / "home"),
                       "BOT_DATA_DIR": str(root / "bot"), "TMPDIR": str(root / "tmp"),
                       "LANG": "C"}
        if self.tier == "local":
            environment["SERVER_ENV"] = "local"
            environment["tc_sdb_nenv"] = "production"
        elif cookie:
            environment["BCS_COOKIE"] = cookie
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
    def _load_json_output(raw: bytes, where: str) -> dict:
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
        except (UnicodeError, json.JSONDecodeError):
            raise OperatorCliRunnerError(
                f"operator CLI {where} did not emit one JSON value") from None
        if not isinstance(value, dict):
            raise OperatorCliRunnerError(f"operator CLI {where} JSON output is not an object")
        return value

    @staticmethod
    def _text(raw: bytes, where: str) -> str:
        try:
            return raw.decode("utf-8")
        except UnicodeError:
            raise OperatorCliRunnerError(
                f"operator CLI {where} output was not UTF-8") from None

    @staticmethod
    def _singleton_line(text: str, label: str) -> str:
        rows = re.findall(r"^  " + re.escape(label) + r": (.*)$", text, re.M)
        if len(rows) != 1 or not rows[0].strip():
            raise OperatorCliRunnerError(
                "operator CLI bounded line output differs")
        return rows[0].strip()

    def _invoke(self, role: str, *, arguments: tuple[str, ...], json_mode: bool,
                onboard_token: str = "", expect_zero: bool = True):
        """Run one bounded CLI capture; returns the projection tuple."""
        credential_kind, token, cookie = self._credentials(
            role, onboard_token=onboard_token)
        root = context = environment = None
        secrets: list[bytes] = []
        for value in (token, cookie):
            if value:
                secrets.append(value.encode())
        primary: BaseException | None = None
        cleanup_status = "NOT_WRITTEN"
        started = time.monotonic()
        raw: bytes = b""
        exit_code = 0
        try:
            root, context, environment = self._context(role, credential_kind, token, cookie)
            command = [str(self.binary), "--url", self.origin]
            if json_mode:
                command.append("--json")
            command += ["--log-level", "error", *arguments]
            result = capture(
                command, cwd=root, environment=environment,
                timeout_seconds=self.profile["policy"]["timeout_seconds"],
                max_output_bytes=self.profile["policy"]["max_response_bytes"],
                collect_stderr=True,
            )
            raw = result.stdout
            # A station face may echo the submitted credential inside a
            # successful live response (connect returns its bot token). The
            # hygiene invariant stays absolute: scrub the value in memory
            # before any projection so no receipt or report can journal it.
            for secret in secrets:
                if secret:
                    raw = raw.replace(secret, b"<redacted-credential>")
            exit_code = result.returncode
            if expect_zero and exit_code != 0:
                raise OperatorCliRunnerError(
                    "operator product CLI returned nonzero: "
                    + (stderr_failure_detail(result, secrets) or "<no stderr>"))
        except ProcessCaptureError as error:
            public = OperatorCliRunnerError("operator product CLI process failed")
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
            failure = OperatorCliRunnerError(
                "operator CLI private context cleanup is unconfirmed")
            failure.credential_cleanup_status = cleanup_status
            if primary is None:
                primary = failure
            else:
                primary.credential_cleanup_status = cleanup_status
        if primary is not None:
            if isinstance(primary, KeyboardInterrupt):
                raise primary
            if not isinstance(primary, OperatorCliRunnerError):
                primary = OperatorCliRunnerError("operator product CLI output is invalid")
            raise primary
        return raw, exit_code, round((time.monotonic() - started) * 1000, 2), credential_kind

    def _parse_visibility(self, raw: bytes) -> str:
        text = self._text(raw, "visibility")
        match = VISIBILITY_LINE.search(text)
        if match is not None:
            return match.group(1)
        try:
            payload = json.loads(text, object_pairs_hook=unique_keys)
        except (UnicodeError, json.JSONDecodeError):
            raise OperatorCliRunnerError(
                "operator CLI visibility output misses its level contract") from None
        value = payload.get("visibility") if isinstance(payload, dict) else None
        if value in OPERATOR_CLI_VISIBILITY_LEVELS:
            return value
        raise OperatorCliRunnerError(
            "operator CLI visibility output misses its level contract")

    def _parse_group_create(self, raw: bytes) -> dict:
        text = self._text(raw, "create-group")
        if not text.startswith("Group created:"):
            raise OperatorCliRunnerError(
                "operator CLI create-group output prefix is invalid")
        found = GROUP_CREATE_ID.search(text)
        if found is None:
            raise OperatorCliRunnerError(
                "operator CLI create-group did not return a Group ID")
        group_id = identifier(found.group(1), "CLI-created Group ID")
        driver = self._singleton_line(text, "Driver")
        participants = [value.strip()
                        for value in self._singleton_line(text, "Participants").split(",")]
        if driver != self.actor_ids["member"] or participants != [
                self.actor_ids["member"], self.actor_ids["eng"]]:
            raise OperatorCliRunnerError(
                "operator CLI create-group driver or participants differ")
        return {"group_id": group_id, "driver_bot": driver,
                "participants": participants, "topic": CREATE_GROUP_TOPIC}

    @staticmethod
    def _identifier(value: object, where: str) -> str:
        try:
            return identifier(value, where)
        except ConfigError as error:
            raise OperatorCliRunnerError(str(error)) from error

    @staticmethod
    def _request_id(payload: dict, where: str) -> str:
        value = payload.get("request_id")
        if not isinstance(value, str) or not value:
            nested = payload.get("data")
            value = nested.get("id") if isinstance(nested, dict) else None
        return OperatorCliRunner._identifier(value, where)

    @staticmethod
    def _already_admitted(payload: dict) -> bool:
        """Match the station's exact idempotent already-friends answer.

        Only the reviewed envelope qualifies: a success carrying the exact
        "Already friends" message with a null data payload and no request
        ID. Every other shape keeps the strict request-ID contract.
        """
        return (payload.get("success") is True
                and payload.get("message") == "Already friends"
                and payload.get("data") is None
                and "request_id" not in payload)

    def _verify_friend_membership(self, role: str, target: str) -> None:
        """Back an already-friends answer with one bounded live read.

        The product CLI emits the bounded friend page as a bare JSON array
        for this verification read; an object page is tolerated as an
        envelope variant; both shapes must contain the exact pair member.
        """
        raw, _, _step, _credential_kind = self._invoke(
            role, arguments=("friend", "list"), json_mode=True)
        try:
            payload = json.loads(raw.decode("utf-8"),
                                 object_pairs_hook=unique_keys)
        except (UnicodeError, json.JSONDecodeError):
            raise OperatorCliRunnerError(
                "operator CLI friend list verification did not emit one JSON "
                "value") from None
        rows = payload if isinstance(payload, list) else payload.get("items")
        if (not isinstance(rows, list)
                or not any(isinstance(row, dict)
                           and row.get("bot_uuid") == target for row in rows)):
            raise OperatorCliRunnerError(
                "already-friends answer is not backed by the live friend list")

    def execute(self, operation: str, *, target: str = "", query: str = "",
                value: str = "public", probe_name: str = "", name: str = "",
                expected_bot_uuid: str = "", onboard_token: str = "",
                bot_uuid: str = "", request_id: str = "",
                group_id: str = "") -> dict:
        if operation not in OPERATOR_CLI_CLI_OPERATIONS:
            raise OperatorCliRunnerError("operation is not a reviewed operator CLI leaf")
        if operation in self.executed:
            raise OperatorCliRunnerError("operator CLI operation was already executed")
        expected_index = OPERATOR_CLI_CLI_OPERATIONS.index(operation)
        if self.executed != list(OPERATOR_CLI_CLI_OPERATIONS[:expected_index]):
            raise OperatorCliRunnerError(
                "operator CLI operations cannot be reordered or skipped")
        if operation == OPERATOR_CLI_HEALTH:
            role = "health"
        elif operation == OPERATOR_CLI_ONBOARD_DIRECT:
            role = "onboard"
        elif operation in {OPERATOR_CLI_CREATE_GROUP, OPERATOR_CLI_GET_GROUP,
                           OPERATOR_CLI_FRIEND_ACCEPT_CHAT}:
            role = "member"
        elif operation == OPERATOR_CLI_FRIEND_ACCEPT_LIST:
            role = "eng"
        else:
            role = "driver"
        arguments: tuple[str, ...] = ()
        data: dict = {}
        elapsed_ms = 0.0
        credential_kind = ""
        if operation == OPERATOR_CLI_HEALTH:
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("health",), json_mode=False)
            elapsed_ms += step
            text = self._text(raw, "health")
            lowered = text.lower()
            if not ("status" in lowered or "ok" in lowered or "healthy" in lowered):
                raise OperatorCliRunnerError(
                    "product CLI health payload contract differs")
            data = {"status": "healthy", "credential_free": True}
            arguments = ("health",)
        elif operation == OPERATOR_CLI_LIST:
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("list",), json_mode=False)
            elapsed_ms += step
            text = self._text(raw, "list")
            for actor_role in OPERATOR_CLI_ACTOR_ROLES:
                if self.actor_ids[actor_role] not in text:
                    raise OperatorCliRunnerError(
                        "product CLI list omits the " + actor_role + " fixture Bot")
            data = {"fixture_uuids": [self.actor_ids[actor_role]
                                      for actor_role in OPERATOR_CLI_ACTOR_ROLES],
                    "fixture_count": len(OPERATOR_CLI_ACTOR_ROLES)}
            arguments = ("list",)
        elif operation == OPERATOR_CLI_GET:
            if not isinstance(target, str) or not target:
                raise OperatorCliRunnerError(
                    "operator CLI get requires the exact Bot ID")
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("get", target), json_mode=False)
            elapsed_ms += step
            if target not in self._text(raw, "get"):
                raise OperatorCliRunnerError(
                    "product CLI get did not echo the requested fixture Bot")
            data = {"target": target, "echoed": True}
            arguments = ("get", target)
        elif operation == OPERATOR_CLI_DISCOVER:
            if not isinstance(query, str) or not query:
                raise OperatorCliRunnerError(
                    "operator CLI discover requires the exact query")
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("discover", "--query", query), json_mode=False)
            elapsed_ms += step
            if self.actor_ids["cs"] not in self._text(raw, "discover"):
                raise OperatorCliRunnerError(
                    "product CLI discover did not return the CS fixture Bot")
            data = {"query": query, "bot_uuid": self.actor_ids["cs"]}
            arguments = ("discover", "--query", query)
        elif operation == OPERATOR_CLI_VISIBILITY:
            if value != "public":
                raise OperatorCliRunnerError(
                    "operator CLI visibility round trip sets only the reviewed public level")
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("visibility", "get"), json_mode=False)
            elapsed_ms += step
            original = self._parse_visibility(raw)
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("visibility", "set", "--value", "public"),
                json_mode=False)
            elapsed_ms += step
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("visibility", "get"), json_mode=False)
            elapsed_ms += step
            if self._parse_visibility(raw) != "public":
                raise OperatorCliRunnerError(
                    "product CLI visibility set public did not take effect")
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("visibility", "set", "--value", original),
                json_mode=False)
            elapsed_ms += step
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("visibility", "get"), json_mode=False)
            elapsed_ms += step
            if self._parse_visibility(raw) != original:
                raise OperatorCliRunnerError(
                    "product CLI visibility exact restore did not take effect")
            data = {"original": original, "round_trip_verified": True}
            arguments = ("visibility", "get")
        elif operation == OPERATOR_CLI_CONNECT:
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("connect",), json_mode=True)
            elapsed_ms += step
            payload = self._load_json_output(raw, "connect")
            if (payload.get("bot_uuid") != self.actor_ids["driver"]
                    or type(payload.get("is_new")) is not bool
                    or not isinstance(payload.get("token"), str)
                    or not payload["token"]):
                raise OperatorCliRunnerError(
                    "product CLI connect response contract differs")
            data = {"bot_uuid": self.actor_ids["driver"],
                    "is_new": payload["is_new"], "token_present": True}
            arguments = ("connect",)
        elif operation == OPERATOR_CLI_ONBOARD_WEB:
            probe = probe_name or "operator-cli-web-" + uuid.uuid4().hex[:6]
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("onboard", "--web", "--name", probe,
                                 "--summary", WEB_ONBOARD_SUMMARY), json_mode=False)
            elapsed_ms += step
            text = self._text(raw, "onboard --web")
            if "register" not in text.lower() or probe not in text:
                raise OperatorCliRunnerError(
                    "product CLI onboard --web did not print the registration URL")
            self.web_probe = probe
            data = {"probe_name": probe, "probe_suffix": probe[-6:],
                    "registered": False}
            arguments = ("onboard", "--web", "--name", probe, "--summary",
                         WEB_ONBOARD_SUMMARY)
        elif operation == OPERATOR_CLI_LIST_AFTER_WEB_ONBOARD:
            probe = probe_name or self.web_probe
            if not isinstance(probe, str) or not probe:
                raise OperatorCliRunnerError(
                    "operator CLI post-web list requires the exact probe name")
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("list",), json_mode=False)
            elapsed_ms += step
            text = self._text(raw, "list after web onboard")
            if probe in text:
                raise OperatorCliRunnerError(
                    "product CLI onboard --web registered a Bot")
            for actor_role in OPERATOR_CLI_ACTOR_ROLES:
                if self.actor_ids[actor_role] not in text:
                    raise OperatorCliRunnerError(
                        "product CLI post-web list omits the "
                        + actor_role + " fixture Bot")
            data = {"probe_absent": True, "fixture_uuids_present": True}
            arguments = ("list",)
        elif operation == OPERATOR_CLI_ONBOARD_DIRECT:
            temp_name = name or "operator-cli-direct-" + uuid.uuid4().hex[:6]
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("onboard", "--name", temp_name, "--summary",
                                 DIRECT_ONBOARD_SUMMARY, "--skills", DIRECT_SKILLS,
                                 "--domains", DIRECT_DOMAINS, "--scopes",
                                 DIRECT_SCOPES), json_mode=True,
                onboard_token=onboard_token)
            elapsed_ms += step
            payload = self._load_json_output(raw, "onboard")
            if not isinstance(expected_bot_uuid, str) or not expected_bot_uuid:
                if not isinstance(payload.get("bot_uuid"), str) or not payload["bot_uuid"]:
                    raise OperatorCliRunnerError(
                        "product CLI direct onboarding returned no Bot ID")
            elif payload.get("bot_uuid") != expected_bot_uuid:
                raise OperatorCliRunnerError(
                    "product CLI direct onboarding did not confirm the registered Bot")
            if payload.get("onboarded") is not True or payload.get("name") != temp_name:
                raise OperatorCliRunnerError(
                    "product CLI direct onboarding response differs")
            data = {"bot_uuid": payload["bot_uuid"], "onboarded": True,
                    "bot_name": temp_name}
            arguments = ("onboard", "--name", temp_name, "--summary",
                         DIRECT_ONBOARD_SUMMARY, "--skills", DIRECT_SKILLS,
                         "--domains", DIRECT_DOMAINS, "--scopes", DIRECT_SCOPES)
        elif operation == OPERATOR_CLI_ONBOARD_READBACK:
            if not isinstance(bot_uuid, str) or not bot_uuid:
                raise OperatorCliRunnerError(
                    "operator CLI onboard readback requires the registered Bot ID")
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("get", bot_uuid), json_mode=False)
            elapsed_ms += step
            if bot_uuid not in self._text(raw, "onboard readback"):
                raise OperatorCliRunnerError(
                    "product CLI get did not read back the onboarded temporary agent")
            data = {"bot_uuid": bot_uuid, "readback_verified": True}
            arguments = ("get", bot_uuid)
        elif operation == OPERATOR_CLI_UPDATE_STATUS:
            acknowledged = []
            for status in ("idle", "busy"):
                raw, _, step, credential_kind = self._invoke(
                    role, arguments=("update-status", "--status", status),
                    json_mode=False)
                elapsed_ms += step
                if "Status" not in self._text(raw, "update-status"):
                    raise OperatorCliRunnerError(
                        "product CLI update-status " + status + " was not accepted")
                acknowledged.append(status)
            data = {"statuses": acknowledged, "acknowledged": True}
            arguments = ("update-status", "--status", "busy")
        elif operation == OPERATOR_CLI_CREATE_GROUP:
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("create-group", "--driver", self.actor_ids["member"],
                                 "--participants",
                                 self.actor_ids["member"] + "," + self.actor_ids["eng"],
                                 "--topic", CREATE_GROUP_TOPIC), json_mode=False)
            elapsed_ms += step
            group = self._parse_group_create(raw)
            self.group_id = group["group_id"]
            data = group
            arguments = ("create-group", "--driver", self.actor_ids["member"],
                         "--participants",
                         self.actor_ids["member"] + "," + self.actor_ids["eng"],
                         "--topic", CREATE_GROUP_TOPIC)
        elif operation == OPERATOR_CLI_GET_GROUP:
            gid = group_id or self.group_id
            if not isinstance(gid, str) or not gid:
                raise OperatorCliRunnerError(
                    "operator CLI get-group requires the exact Group ID")
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("get-group", "--id", gid), json_mode=False)
            elapsed_ms += step
            text = self._text(raw, "get-group")
            if not (gid in text and self.actor_ids["member"] in text
                    and self.actor_ids["eng"] in text):
                raise OperatorCliRunnerError(
                    "product CLI get-group did not echo the exact owned Group")
            data = {"group_id": gid, "echoed": True}
            arguments = ("get-group", "--id", gid)
        elif operation in {OPERATOR_CLI_FRIEND_REQUEST_CHAT,
                           OPERATOR_CLI_FRIEND_REQUEST_LIST}:
            target = (self.actor_ids["member"]
                      if operation == OPERATOR_CLI_FRIEND_REQUEST_CHAT
                      else self.actor_ids["eng"])
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("friend", "request", "--bot-uuid", target),
                json_mode=True)
            elapsed_ms += step
            payload = self._load_json_output(raw, "friend request")
            if self._already_admitted(payload):
                # A stateful station retains this case's fixture pair (the
                # preprod friendships are permanent while the disclosed
                # revoke asymmetry stands). Record the station's exact
                # idempotent answer, back it with one bounded live
                # membership read, and never fabricate a pending request ID.
                self._verify_friend_membership(role, target)
                accepted = None
                admission = "ALREADY_ADMITTED"
            else:
                accepted = self._request_id(payload, "friend request ID")
                admission = "ACCEPTED"
            if operation == OPERATOR_CLI_FRIEND_REQUEST_CHAT:
                self.chat_request_id = accepted
                self.chat_admission = admission
            else:
                self.list_request_id = accepted
                self.list_admission = admission
            data = {"request_id": accepted, "admission": admission,
                    "friends_verified": admission == "ALREADY_ADMITTED",
                    "target_bot": target}
            arguments = ("friend", "request", "--bot-uuid", target)
        elif operation in {OPERATOR_CLI_FRIEND_ACCEPT_CHAT,
                           OPERATOR_CLI_FRIEND_ACCEPT_LIST}:
            chat_leg = operation == OPERATOR_CLI_FRIEND_ACCEPT_CHAT
            accepted = request_id or (
                self.chat_request_id if chat_leg else self.list_request_id)
            admission = self.chat_admission if chat_leg else self.list_admission
            if accepted is None and admission == "ALREADY_ADMITTED":
                # The request leg proved the pair is already admitted
                # station-side; no pending request exists for the accept
                # route to decide. Record the substitution; invoke nothing
                # and fabricate no request ID.
                credential_kind = ""
                data = {"request_id": None, "admission": "ALREADY_ADMITTED",
                        "accept_invoked": False, "accepted": True}
                arguments = ()
            else:
                if not isinstance(accepted, str) or not accepted:
                    raise OperatorCliRunnerError(
                        "operator CLI friend accept requires the exact request ID")
                raw, _, step, credential_kind = self._invoke(
                    role, arguments=("friend", "accept",
                                     "--request-id", accepted),
                    json_mode=False)
                elapsed_ms += step
                data = {"request_id": accepted, "admission": "ACCEPTED",
                        "accept_invoked": True, "accepted": True}
                arguments = ("friend", "accept", "--request-id", accepted)
        elif operation == OPERATOR_CLI_CHAT:
            detach_arguments = ("chat", "--bot-uuid", self.actor_ids["member"],
                                "--message", CHAT_MESSAGE, "--detach")
            raw, exit_code, step, credential_kind = self._invoke(
                role, arguments=detach_arguments, json_mode=True, expect_zero=False)
            elapsed_ms += step
            fallback = False
            if exit_code != 0:
                fallback = True
                blocking_arguments = ("chat", "--bot-uuid", self.actor_ids["member"],
                                      "--message", CHAT_MESSAGE)
                raw, _, step, credential_kind = self._invoke(
                    role, arguments=blocking_arguments, json_mode=True)
                elapsed_ms += step
                detach_arguments = blocking_arguments
            payload = self._load_json_output(raw, "chat")
            run_id = self._identifier(payload.get("run_id"), "CLI chat run ID")
            session_id = self._identifier(payload.get("session_id"),
                                          "CLI chat session ID")
            data = {"run_id": run_id, "session_id": session_id,
                    "detach_fallback": fallback}
            arguments = detach_arguments
        elif operation == OPERATOR_CLI_LIST_GROUPS:
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("list-groups",), json_mode=True)
            elapsed_ms += step
            payload = self._load_json_output(raw, "list-groups")
            items = payload.get("items")
            returned = payload.get("returned")
            total = payload.get("total")
            has_more = payload.get("has_more")
            if (not isinstance(items, list) or type(returned) is not int
                    or returned != len(items) or type(total) is not int
                    or total < returned or type(has_more) is not bool
                    or any(not isinstance(item, dict)
                           or not (item.get("group_id") or item.get("id"))
                           for item in items)):
                raise OperatorCliRunnerError(
                    "product CLI list-groups page shape differs")
            data = {"returned": returned, "total": total, "has_more": has_more,
                    "item_count": len(items)}
            arguments = ("list-groups",)
        elif operation == OPERATOR_CLI_FRIEND_LIST:
            raw, _, step, credential_kind = self._invoke(
                role, arguments=("friend", "list"), json_mode=False)
            elapsed_ms += step
            if self.actor_ids["eng"] not in self._text(raw, "friend list"):
                raise OperatorCliRunnerError(
                    "product CLI friend list did not contain the ENG fixture Bot")
            data = {"friend_list_verified": True, "target_bot": self.actor_ids["eng"]}
            arguments = ("friend", "list")
        method, route = OPERATOR_CLI_ROUTES[operation]
        self.executed.append(operation)
        evidence = {
            "transport": "PRODUCT_CLI",
            "operation": operation,
            "caller_role": role,
            "command_arguments": list(arguments),
            "http_method_declared": method,
            "route_declared": route,
            "credential_kind": credential_kind,
            "exit_status": 0,
            "elapsed_ms": elapsed_ms,
            "fixture_effect": OPERATOR_CLI_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        }
        return {"data": data, "evidence": evidence}
