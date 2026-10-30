"""Source-anchored field-level density obligations for the retired service policy story.

The upstream owned-local black-box story
(``apps/bcs/scripts/e2e-test/service_policy_story.py``, marker caliber 23
charged checks: 19 request-status checks plus 4 policy-absence checks) drives
one frozen legacy journey: bootstrap a disposable local driver principal,
create two Groups whose legacy ``service_spec`` encodings (a disabled policy
object and a null compatibility shim) must persist nothing and never leak
into responses, keep the retired PATCH settings surface Gone (410
``service_policy_retired``), prove two distinct ``service_invocation``
Sessions run with persisted inputs despite the legacy policies, refuse
reactivation of a running invocation (409 running-shaped), refuse converting a
completed chat Session into a service invocation (400
``not service_invocation``), and dispose every resource on teardown.

The committed flat migration contract binds that journey as eight coarse
``SERVICE-POLICY.*`` parent obligations with 23 story checks; this projection
adds the density layer without touching the frozen flat contract:

* eight PARENT obligations stay 1:1 with the frozen ``SERVICE-POLICY.*`` map
  and each charges its representative upstream request site directly;
* thirty-two FIELD obligations project the charged marker checks and the
  compound upstream Python asserts: the four policy-absence checks split into
  their two upstream field asserts each per encoding-iteration call site
  (4 checks -> 8 children per legacy shape, 12 per story phase), compound
  asserts split per field (session kind, lifecycle status, stored input,
  distinct ids, error shapes, final status/output), and every row anchors to
  its ``service_policy_story.py`` line with the exact upstream quote;
* the marker caliber 23 is the upstream story's own counter (the marker
  prints before ``cleanup()``, so the six teardown dispositions ride the
  cleanup parent and are never silently marker-charged);
* the committed bearer leg caliber (20 checks: one driver identity
  confirmation plus 19 business checks, registration plane
  ``REMOTE_NOT_APPLICABLE_PRE_ADMITTED_DRIVER``) is disclosed in the
  reconciliation, never merged into the local caliber;
* shell/python probes without an assertion role are recorded as
  ``NOT_CHARGED_SOURCE_PROBES`` and never counted as coverage;
* nothing parsed-but-unlocated exists, so ``parsed_unlocated`` is honestly
  empty and the projection is a declared superset (40 > 23) by
  compound-assert expansion and absence-check splitting, disclosed by
  ``service_policy_density_reconciliation``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..local_service_policy_contract import REQUIREMENTS
from ..run_state import RunStateError

PARENT = "story_retired_service_policy"
SOURCE_FILE = "apps/bcs/scripts/e2e-test/service_policy_story.py"
SOURCE_PATH = Path(__file__).resolve().parents[2].parent / "e2e-test" / "service_policy_story.py"

COARSE_PARENT_COUNT = 8
FIELD_OBLIGATION_COUNT = 32
# One anchored upstream field site projects exactly one field obligation
# here (the four absence checks already split per encoding per call
# site); the delivery slice projects two obligations per site instead.
FIELD_SITE_COUNT = 32
PARSED_OBLIGATION_COUNT = COARSE_PARENT_COUNT + FIELD_OBLIGATION_COUNT

# Upstream marker caliber (the story's own ``self.checks`` counter) and the
# committed bearer-leg caliber (service_policy/contract.py).
MARKER_CHARGED_CHECKS = 23
BEARER_STORY_CHECKS = 20

STATUS_KIND = "status"
FIELD_KIND = "field"
ABSENT_COMPARATOR = "field_absent"
EQUALS_COMPARATOR = "field_equals"
CONTAINS_COMPARATOR = "field_contains"
DISTINCT_COMPARATOR = "session_ids_distinct"
TEARDOWN_COMPARATOR = "teardown_dispositions"

BOOTSTRAP = "BOOTSTRAP"
LEGACY = "LEGACY"
RETIRED = "RETIRED"
INVOCATION = "INVOCATION"
GUARD = "GUARD"
CONVERSION = "CONVERSION"
TEARDOWN = "TEARDOWN"

# (requirement, family, assert_line, assert_quote, request_line, request_quote,
#  method, path, caller, expected, comparator, operation, expectation)
_PARENT_SITES: tuple[tuple[str, str, int, str, int, str, str, str, str, Any, str, str, str], ...] = (
    ("SERVICE-POLICY.mock-human-bootstrap", BOOTSTRAP, 57,
     'self.request("POST", "/me/ensure-human", {}, bot_auth=False)',
     57, 'self.request("POST", "/me/ensure-human", {}, bot_auth=False)',
     "POST", "/me/ensure-human", "human", 200, "http_status",
     "service_policy.ensure_human",
     "The loopback mock-admission bootstrap confirms the story Human principal through POST /me/ensure-human."),
    ("SERVICE-POLICY.provider-free-bot-registration", BOOTSTRAP, 59,
     'registered = self.request(',
     60, 'f"/register?token={segment(token)}&bot-name=Policy-story-driver"',
     "POST", "/register", "human", 200, "http_status",
     "service_policy.register_bot",
     "The owned-local register plane mints one disposable driver Bot with a one-time token; the preprod leg maps this obligation to REMOTE_NOT_APPLICABLE_PRE_ADMITTED_DRIVER and never registers."),
    ("SERVICE-POLICY.legacy-encoding-ignored", LEGACY, 70,
     'created = self.request("POST", "/groups", {',
     70, 'created = self.request("POST", "/groups", {',
     "POST", "/groups", "driver", (200, 201), "http_status_enum",
     "service_policy.group_create_object",
     "Both legacy service_spec encodings (disabled object and null shim) are accepted by POST /groups on two disposable Groups while persisting nothing."),
    ("SERVICE-POLICY.retired-settings-gone", RETIRED, 82,
     '"service_spec": {"enabled": True}}, status=410',
     81, 'retired = self.request("PATCH", f"/groups/{group}/settings",',
     "PATCH", "/groups/{group_id}/settings", "driver", 410, "http_status",
     "service_policy.settings_patch_retired",
     "The retired PATCH settings surface answers 410 with error service_policy_retired and never reactivates a policy."),
    ("SERVICE-POLICY.invocation-lifecycle", INVOCATION, 88,
     "status=202",
     86, 'invoked = self.request("POST", f"/services/{group}/sessions", {',
     "POST", "/services/{group_id}/sessions", "driver", 202, "http_status",
     "service_policy.invocation_create_first",
     "Legacy policies never block service invocation: two distinct service_invocation Sessions answer 202 and persist their submitted inputs."),
    ("SERVICE-POLICY.running-reactivation-denied", GUARD, 100,
     "status=409",
     99, 'conflict = self.request("POST", f"/services/{group}/sessions",',
     "POST", "/services/{group_id}/sessions", "driver", 409, "http_status",
     "service_policy.reactivation_denied",
     "Reactivating a running invocation answers 409 with a running-shaped error and leaves the stored input untouched."),
    ("SERVICE-POLICY.chat-session-guard", CONVERSION, 106,
     "status=201",
     105, 'chat = self.request("POST", f"/groups/{group}/sessions",',
     "POST", "/groups/{group_id}/sessions", "driver", 201, "http_status",
     "service_policy.chat_create",
     "A completed chat Session cannot convert into a service invocation: creation, completion, refusal (400 not service_invocation) and final intactness."),
    ("SERVICE-POLICY.cleanup-disposition", TEARDOWN, 129,
     'assert not failures, f"Story cleanup failed: {failures}"',
     120, 'resources = [f"/sessions/{sid}?bot_id={segment(self.bot)}" for sid in self.sessions]',
     "PROJECTED", "owned-local-teardown-projection", "driver", None, "teardown_dispositions",
     "service_policy.teardown_dispositions",
     "Teardown deletes the three disposable Sessions, the two disposable Groups and (owned-local only) the registered driver Bot; every disposition answers 200/204 and the preprod leg never deletes the shared driver Bot."),
)

# (short_id, parent, label, assert_line, assert_quote, request_line,
#  request_quote, field, comparator, expected, comparator_text, operation,
#  method, path, expectation)
_FIELD_SITES: tuple[tuple[str, str, str, int, str, int, str, str, str, Any, str, str, str, str, str], ...] = (
    ("F01", "SERVICE-POLICY.legacy-encoding-ignored", "object encoding Group creation answers 200/201",
     70, 'created = self.request("POST", "/groups", {', 70,
     'created = self.request("POST", "/groups", {',
     None, "http_status_enum", (200, 201), "200 or 201",
     "service_policy.group_create_object", "POST", "/groups",
     "Legacy object-encoding Group creation is charged with an accepted status."),
    ("F02", "SERVICE-POLICY.legacy-encoding-ignored", "created object-encoding Group hides service_spec",
     52, 'assert "service_spec" not in group, "Retired policy leaked into Group response"', 70,
     'created = self.request("POST", "/groups", {',
     "service_spec", ABSENT_COMPARATOR, None, "the field is absent",
     "service_policy.group_create_object", "POST", "/groups",
     "The created response of the object-encoded Group never exposes service_spec."),
    ("F03", "SERVICE-POLICY.legacy-encoding-ignored", "created object-encoding Group hides retired_service_spec",
     53, 'assert "retired_service_spec" not in group, "Compatibility shim leaked into response"', 70,
     'created = self.request("POST", "/groups", {',
     "retired_service_spec", ABSENT_COMPARATOR, None, "the field is absent",
     "service_policy.group_create_object", "POST", "/groups",
     "The created response of the object-encoded Group never exposes the compatibility shim field."),
    ("F04", "SERVICE-POLICY.legacy-encoding-ignored", "object encoding read-back answers 200",
     78, 'self.assert_policy_absent(self.request("GET", f"/groups/{group}"))', 78,
     'self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     None, "http_status", 200, "http_status 200",
     "service_policy.group_read_object", "GET", "/groups/{group_id}",
     "The object-encoded Group read-back answers 200."),
    ("F05", "SERVICE-POLICY.legacy-encoding-ignored", "read-back object-encoding Group hides service_spec",
     52, 'assert "service_spec" not in group, "Retired policy leaked into Group response"', 78,
     'self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     "service_spec", ABSENT_COMPARATOR, None, "the field is absent",
     "service_policy.group_read_object", "GET", "/groups/{group_id}",
     "The persisted object-encoded Group never leaks service_spec on read-back."),
    ("F06", "SERVICE-POLICY.legacy-encoding-ignored", "read-back object-encoding Group hides retired_service_spec",
     53, 'assert "retired_service_spec" not in group, "Compatibility shim leaked into response"', 78,
     'self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     "retired_service_spec", ABSENT_COMPARATOR, None, "the field is absent",
     "service_policy.group_read_object", "GET", "/groups/{group_id}",
     "The persisted object-encoded Group never leaks the compatibility shim on read-back."),
    ("F07", "SERVICE-POLICY.legacy-encoding-ignored", "null encoding Group creation answers 200/201",
     70, 'created = self.request("POST", "/groups", {', 70,
     'created = self.request("POST", "/groups", {',
     None, "http_status_enum", (200, 201), "200 or 201",
     "service_policy.group_create_null", "POST", "/groups",
     "Legacy null-encoding (compatibility shim) Group creation is charged with an accepted status."),
    ("F08", "SERVICE-POLICY.legacy-encoding-ignored", "created null-encoding Group hides service_spec",
     52, 'assert "service_spec" not in group, "Retired policy leaked into Group response"', 70,
     'created = self.request("POST", "/groups", {',
     "service_spec", ABSENT_COMPARATOR, None, "the field is absent",
     "service_policy.group_create_null", "POST", "/groups",
     "The created response of the null-encoded Group never exposes service_spec."),
    ("F09", "SERVICE-POLICY.legacy-encoding-ignored", "created null-encoding Group hides retired_service_spec",
     53, 'assert "retired_service_spec" not in group, "Compatibility shim leaked into response"', 70,
     'created = self.request("POST", "/groups", {',
     "retired_service_spec", ABSENT_COMPARATOR, None, "the field is absent",
     "service_policy.group_create_null", "POST", "/groups",
     "The created response of the null-encoded Group never exposes the compatibility shim field."),
    ("F10", "SERVICE-POLICY.legacy-encoding-ignored", "null encoding read-back answers 200",
     78, 'self.assert_policy_absent(self.request("GET", f"/groups/{group}"))', 78,
     'self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     None, "http_status", 200, "http_status 200",
     "service_policy.group_read_null", "GET", "/groups/{group_id}",
     "The null-encoded Group read-back answers 200."),
    ("F11", "SERVICE-POLICY.legacy-encoding-ignored", "read-back null-encoding Group hides service_spec",
     52, 'assert "service_spec" not in group, "Retired policy leaked into Group response"', 78,
     'self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     "service_spec", ABSENT_COMPARATOR, None, "the field is absent",
     "service_policy.group_read_null", "GET", "/groups/{group_id}",
     "The persisted null-encoded Group never leaks service_spec on read-back."),
    ("F12", "SERVICE-POLICY.legacy-encoding-ignored", "read-back null-encoding Group hides retired_service_spec",
     53, 'assert "retired_service_spec" not in group, "Compatibility shim leaked into response"', 78,
     'self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     "retired_service_spec", ABSENT_COMPARATOR, None, "the field is absent",
     "service_policy.group_read_null", "GET", "/groups/{group_id}",
     "The persisted null-encoded Group never leaks the compatibility shim on read-back."),
    ("F13", "SERVICE-POLICY.retired-settings-gone", "retired settings patch answers 410",
     82, '"service_spec": {"enabled": True}}, status=410', 81,
     'retired = self.request("PATCH", f"/groups/{group}/settings",',
     None, "http_status", 410, "http_status 410",
     "service_policy.settings_patch_retired", "PATCH", "/groups/{group_id}/settings",
     "The retired settings surface answers 410 Gone."),
    ("F14", "SERVICE-POLICY.retired-settings-gone", "retired settings patch carries the exact error",
     83, 'assert retired["error"] == "service_policy_retired", retired', 81,
     'retired = self.request("PATCH", f"/groups/{group}/settings",',
     "error", EQUALS_COMPARATOR, "service_policy_retired",
     "the exact error code service_policy_retired",
     "service_policy.settings_patch_retired", "PATCH", "/groups/{group_id}/settings",
     "The 410 envelope carries the exact service_policy_retired error code."),
    ("F15", "SERVICE-POLICY.invocation-lifecycle", "first invocation creation answers 202",
     88, "status=202", 86,
     'invoked = self.request("POST", f"/services/{group}/sessions", {',
     None, "http_status", 202, "http_status 202",
     "service_policy.invocation_create_first", "POST", "/services/{group_id}/sessions",
     "The first legacy-policy Group still admits service invocation creation with 202."),
    ("F16", "SERVICE-POLICY.invocation-lifecycle", "first invocation Session kind is service_invocation",
     91, 'assert invoked["session_kind"] == "service_invocation", invoked', 86,
     'invoked = self.request("POST", f"/services/{group}/sessions", {',
     "session_kind", EQUALS_COMPARATOR, "service_invocation", "service_invocation",
     "service_policy.invocation_create_first", "POST", "/services/{group_id}/sessions",
     "The first invocation Session identifies as service_invocation."),
    ("F17", "SERVICE-POLICY.invocation-lifecycle", "first invocation lifecycle status is running",
     92, 'assert invoked["status"] == "running", invoked', 86,
     'invoked = self.request("POST", f"/services/{group}/sessions", {',
     "status", EQUALS_COMPARATOR, "running", "running",
     "service_policy.invocation_create_first", "POST", "/services/{group_id}/sessions",
     "The first invocation Session reports the running lifecycle status."),
    ("F18", "SERVICE-POLICY.invocation-lifecycle", "first invocation persists input run 0",
     94, 'assert stored["input"] == {"run": index}, stored', 93,
     'stored = self.request("GET", f"/services/{group}/sessions/{sid}")',
     "input", EQUALS_COMPARATOR, "RUN_ZERO", '{"run": 0}',
     "service_policy.invocation_read_first", "GET", "/services/{group_id}/sessions/{session_id}",
     "The first invocation Session persists the submitted input {\"run\": 0}."),
    ("F19", "SERVICE-POLICY.invocation-lifecycle", "second invocation creation answers 202",
     88, "status=202", 86,
     'invoked = self.request("POST", f"/services/{group}/sessions", {',
     None, "http_status", 202, "http_status 202",
     "service_policy.invocation_create_second", "POST", "/services/{group_id}/sessions",
     "The second legacy-policy Group still admits service invocation creation with 202."),
    ("F20", "SERVICE-POLICY.invocation-lifecycle", "second invocation Session kind is service_invocation",
     91, 'assert invoked["session_kind"] == "service_invocation", invoked', 86,
     'invoked = self.request("POST", f"/services/{group}/sessions", {',
     "session_kind", EQUALS_COMPARATOR, "service_invocation", "service_invocation",
     "service_policy.invocation_create_second", "POST", "/services/{group_id}/sessions",
     "The second invocation Session identifies as service_invocation."),
    ("F21", "SERVICE-POLICY.invocation-lifecycle", "second invocation lifecycle status is running",
     92, 'assert invoked["status"] == "running", invoked', 86,
     'invoked = self.request("POST", f"/services/{group}/sessions", {',
     "status", EQUALS_COMPARATOR, "running", "running",
     "service_policy.invocation_create_second", "POST", "/services/{group_id}/sessions",
     "The second invocation Session reports the running lifecycle status."),
    ("F22", "SERVICE-POLICY.invocation-lifecycle", "second invocation persists input run 1",
     94, 'assert stored["input"] == {"run": index}, stored', 93,
     'stored = self.request("GET", f"/services/{group}/sessions/{sid}")',
     "input", EQUALS_COMPARATOR, "RUN_ONE", '{"run": 1}',
     "service_policy.invocation_read_second", "GET", "/services/{group_id}/sessions/{session_id}",
     "The second invocation Session persists the submitted input {\"run\": 1}."),
    ("F23", "SERVICE-POLICY.invocation-lifecycle", "the two invocations never share a Session id",
     95, 'assert self.sessions[0] != self.sessions[1], "Invocations unexpectedly reused a Session"', 93,
     'stored = self.request("GET", f"/services/{group}/sessions/{sid}")',
     "session_id", DISTINCT_COMPARATOR, None, "two distinct session ids",
     "service_policy.invocation_read_second", "GET", "/services/{group_id}/sessions/{session_id}",
     "The two invocations never reuse one Session id."),
    ("F24", "SERVICE-POLICY.running-reactivation-denied", "reactivation refusal answers 409",
     100, "status=409", 99,
     'conflict = self.request("POST", f"/services/{group}/sessions",',
     None, "http_status", 409, "http_status 409",
     "service_policy.reactivation_denied", "POST", "/services/{group_id}/sessions",
     "Reactivating the running first invocation answers 409."),
    ("F25", "SERVICE-POLICY.running-reactivation-denied", "reactivation error is running-shaped",
     101, 'assert "running" in conflict["error"], conflict', 99,
     'conflict = self.request("POST", f"/services/{group}/sessions",',
     "error", CONTAINS_COMPARATOR, "running", "an error containing running",
     "service_policy.reactivation_denied", "POST", "/services/{group_id}/sessions",
     "The refusal error is running-shaped (contains running)."),
    ("F26", "SERVICE-POLICY.running-reactivation-denied", "refused reactivation preserves input run 0",
     102, 'assert self.request("GET", f"/services/{group}/sessions/{sid}")["input"] == {"run": 0}', 102,
     'assert self.request("GET", f"/services/{group}/sessions/{sid}")["input"] == {"run": 0}',
     "input", EQUALS_COMPARATOR, "RUN_ZERO", '{"run": 0}',
     "service_policy.reactivation_read", "GET", "/services/{group_id}/sessions/{session_id}",
     "The refused reactivation leaves the first invocation input {\"run\": 0} intact."),
    ("F27", "SERVICE-POLICY.chat-session-guard", "chat Session creation answers 201",
     106, "status=201", 105,
     'chat = self.request("POST", f"/groups/{group}/sessions",',
     None, "http_status", 201, "http_status 201",
     "service_policy.chat_create", "POST", "/groups/{group_id}/sessions",
     "The guard journey creates a chat Session with 201."),
    ("F28", "SERVICE-POLICY.chat-session-guard", "chat Session completion status is completed",
     110, 'assert completed["status"] == "completed", completed', 109,
     'completed = self.request("POST", f"/sessions/{sid}/complete", {"output": {"ok": True}})',
     "status", EQUALS_COMPARATOR, "completed", "completed",
     "service_policy.chat_complete", "POST", "/sessions/{session_id}/complete",
     "The chat Session completes with status completed."),
    ("F29", "SERVICE-POLICY.chat-session-guard", "conversion refusal answers 400",
     112, "status=400", 111,
     'invalid = self.request("POST", f"/services/{group}/sessions",',
     None, "http_status", 400, "http_status 400",
     "service_policy.conversion_denied", "POST", "/services/{group_id}/sessions",
     "Converting the completed chat Session into a service invocation answers 400."),
    ("F30", "SERVICE-POLICY.chat-session-guard", "conversion error names not service_invocation",
     113, 'assert "not service_invocation" in invalid["error"], invalid', 111,
     'invalid = self.request("POST", f"/services/{group}/sessions",',
     "error", CONTAINS_COMPARATOR, "not service_invocation",
     "an error containing not service_invocation",
     "service_policy.conversion_denied", "POST", "/services/{group_id}/sessions",
     "The refusal error names the not service_invocation cause."),
    ("F31", "SERVICE-POLICY.chat-session-guard", "final chat Session status stays completed",
     115, 'assert stored["status"] == "completed" and stored["output"] == {"ok": True}, stored', 114,
     'stored = self.request("GET", f"/sessions/{sid}")',
     "status", EQUALS_COMPARATOR, "completed", "completed",
     "service_policy.chat_final_read", "GET", "/sessions/{session_id}",
     "The refused conversion leaves the chat Session status completed."),
    ("F32", "SERVICE-POLICY.chat-session-guard", "final chat Session output stays intact",
     115, 'assert stored["status"] == "completed" and stored["output"] == {"ok": True}, stored', 114,
     'stored = self.request("GET", f"/sessions/{sid}")',
     "output", EQUALS_COMPARATOR, "CHAT_OUTPUT", '{"ok": true}',
     "service_policy.chat_final_read", "GET", "/sessions/{session_id}",
     "The refused conversion leaves the chat Session output {\"ok\": true} intact."),
)

NOT_CHARGED_SOURCE_PROBES: tuple[dict[str, Any], ...] = (
    {"source_line": 58, "source_quote": 'token = self.request("GET", "/register/token", bot_auth=False)["token"]',
     "reason": "one-time token extraction feeding the register request; derivation, not an assertion"},
    {"source_line": 63, "source_quote": 'self.bot, self.bot_token = registered["bot_uuid"], registered["bot_token"]',
     "reason": "registered credential unpack; extraction, never journaled, not an assertion"},
    {"source_line": 75, "source_quote": 'group = created["id"]',
     "reason": "created Group id extraction feeding the read-back and teardown; derivation, not an assertion"},
    {"source_line": 80, "source_quote": "group = self.groups[0]",
     "reason": "first disposable Group selection for the settings/invocation/guard phases; derivation, not an assertion"},
    {"source_line": 89, "source_quote": 'sid = invoked["session_id"]',
     "reason": "invocation Session id extraction feeding the stored-input read; derivation, not an assertion"},
    {"source_line": 98, "source_quote": "sid = self.sessions[0]",
     "reason": "first invocation id selection for the reactivation refusal; derivation, not an assertion"},
    {"source_line": 107, "source_quote": 'sid = chat["session_id"]',
     "reason": "chat Session id extraction feeding completion and the conversion refusal; derivation, not an assertion"},
    {"source_line": 116, "source_quote": "print(f\"Retired service policy story: {self.checks} HTTP/response checks passed\", flush=True)",
     "reason": "the marker line itself; the 23-check marker caliber is disclosed in the reconciliation, never double-charged"},
)


@dataclass(frozen=True)
class DensityObligation:
    """One executable projection row anchored to a service_policy_story.py line."""

    obligation_id: str
    level: str
    parent_obligation: str
    family: str
    kind: str
    source_line: int
    source_quote: str
    request_line: int
    request_quote: str
    operation: str
    method: str
    path: str
    caller: str
    field: str | None
    comparator: str
    expected: Any
    comparator_text: str
    expectation: str
    density: bool
    preprod_mode: str
    located: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "level": self.level,
            "parent_obligation": self.parent_obligation,
            "family": self.family,
            "kind": self.kind,
            "source_file": SOURCE_FILE,
            "source_line": self.source_line,
            "source_quote": self.source_quote,
            "request_line": self.request_line,
            "request_quote": self.request_quote,
            "operation": self.operation,
            "method": self.method,
            "path": self.path,
            "caller": self.caller,
            "field": self.field,
            "comparator": self.comparator,
            "expected": self.expected if not isinstance(self.expected, tuple) else list(self.expected),
            "comparator_text": self.comparator_text,
            "expectation": self.expectation,
            "density": self.density,
            "preprod_mode": self.preprod_mode,
            "located": self.located,
        }


def _read_source() -> list[str]:
    try:
        return SOURCE_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RunStateError(f"service policy density source is unavailable: {error}") from None


def _anchor(lines: list[str], line_no: int, quote: str, obligation_id: str) -> None:
    if line_no < 1 or line_no > len(lines) or quote not in lines[line_no - 1]:
        raise RunStateError(
            f"service policy density source anchor changed: {obligation_id} "
            f"(service_policy_story.py:{line_no})")


def build_service_policy_density_obligations() -> tuple[DensityObligation, ...]:
    """Build and anchor-validate the 40-point obligation roster.

    The roster freezes eight parent obligations 1:1 with the committed flat
    ``SERVICE-POLICY.*`` contract and projects thirty-two field obligations
    from the upstream story's charged marker checks and compound asserts.
    Any anchor drift in ``service_policy_story.py`` fails closed instead of
    silently regressing coverage.
    """
    lines = _read_source()
    if not lines[55].startswith("    def run(self) -> None:"):
        raise RunStateError("service policy density story run() header changed")
    if "HTTP/response checks passed" not in lines[115]:
        raise RunStateError("service policy density story marker line changed")
    parent_ids = list(REQUIREMENTS)
    rows: list[DensityObligation] = []
    for (requirement, family, assert_line, assert_quote, request_line, request_quote,
         method, path, caller, expected, comparator, operation,
         expectation) in _PARENT_SITES:
        if requirement != parent_ids[len(rows)]:
            raise RunStateError("service policy density parent projection changed")
        _anchor(lines, assert_line, assert_quote, requirement)
        _anchor(lines, request_line, request_quote, requirement)
        rows.append(DensityObligation(
            obligation_id=requirement, level="PARENT", parent_obligation=requirement,
            family=family, kind=STATUS_KIND, source_line=assert_line, source_quote=assert_quote,
            request_line=request_line, request_quote=request_quote, operation=operation,
            method=method, path=path, caller=caller, field=None, comparator=comparator,
            expected=expected, comparator_text="http_status", expectation=expectation,
            density=False, preprod_mode=_preprod_mode(requirement)))
    known = {row.obligation_id for row in rows}
    for (short_id, parent, label, assert_line, assert_quote, request_line, request_quote,
         field, comparator, expected, comparator_text, operation, method, path,
         expectation) in _FIELD_SITES:
        if parent not in known:
            raise RunStateError(f"service policy density field site {short_id} references unknown parent {parent}")
        _anchor(lines, assert_line, assert_quote, f"SERVICE-POLICY.{short_id}")
        _anchor(lines, request_line, request_quote, f"SERVICE-POLICY.{short_id}")
        rows.append(DensityObligation(
            obligation_id=f"SERVICE-POLICY.{short_id}", level="FIELD", parent_obligation=parent,
            family=_family_by_parent[parent], kind=FIELD_KIND, source_line=assert_line,
            source_quote=assert_quote, request_line=request_line, request_quote=request_quote,
            operation=operation, method=method, path=path, caller="driver", field=field,
            comparator=comparator, expected=expected, comparator_text=comparator_text,
            expectation=expectation, density=True,
            preprod_mode="LIVE_BEARER_REPLAY"))
    if len(rows) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("service policy density parsed obligation count changed")
    if len({row.obligation_id for row in rows}) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("service policy density obligation ids are not unique")
    return tuple(rows)


_family_by_parent = {
    "SERVICE-POLICY.mock-human-bootstrap": BOOTSTRAP,
    "SERVICE-POLICY.provider-free-bot-registration": BOOTSTRAP,
    "SERVICE-POLICY.legacy-encoding-ignored": LEGACY,
    "SERVICE-POLICY.retired-settings-gone": RETIRED,
    "SERVICE-POLICY.invocation-lifecycle": INVOCATION,
    "SERVICE-POLICY.running-reactivation-denied": GUARD,
    "SERVICE-POLICY.chat-session-guard": CONVERSION,
    "SERVICE-POLICY.cleanup-disposition": TEARDOWN,
}


def _preprod_mode(requirement: str) -> str:
    if requirement == REQUIREMENTS[0]:
        return "BEARER_IDENTITY_CONFIRM"
    if requirement == REQUIREMENTS[1]:
        return "REMOTE_NOT_APPLICABLE_PRE_ADMITTED_DRIVER"
    return "LIVE_BEARER_REPLAY"


def service_policy_density_reconciliation() -> dict[str, Any]:
    """Disclose every parsed-obligation caliber honestly.

    Three calibers coexist and are never merged: the upstream marker caliber
    23 (the story's own charged counter), the committed bearer-leg caliber 20
    (one driver identity confirmation plus 19 business checks with the
    registration plane honestly not applicable on shared preprod), and this
    projection's 40-point roster (a declared superset: the four absence
    checks split into eight per-encoding per-call-site field children, the
    compound upstream Python asserts split per field, and the two bootstrap
    parents charge their own request sites).
    """
    return {
        "coverage_map_row": "coverage-map/coverage-map-20261031.json no=19 story_retired_service_policy",
        "coverage_map_snapshot_parsed_count": 23,
        "coverage_map_snapshot_basis": (
            "upstream marker caliber: the story's own self.checks counter "
            "(19 request-status checks plus 4 policy-absence checks); the "
            "marker prints before cleanup() so teardown is never marker-charged"),
        "bearer_leg_story_checks": BEARER_STORY_CHECKS,
        "bearer_leg_basis": (
            "committed service_policy/contract.py BEARER_STORY_CHECKS: one "
            "driver identity confirmation plus 19 business checks; the "
            "registration plane maps to REMOTE_NOT_APPLICABLE_PRE_ADMITTED_DRIVER"),
        "raw_anchored_sites": COARSE_PARENT_COUNT + 32,
        "raw_status_sites": COARSE_PARENT_COUNT,
        "raw_field_sites": FIELD_OBLIGATION_COUNT,
        "projection_rule": (
            "parents stay 1:1 with the eight frozen SERVICE-POLICY.* coarse "
            "obligations and each charges its representative upstream request "
            "site directly; the four policy-absence checks split into two "
            "field obligations each per encoding iteration per call site "
            "(8 per shape, 8 total read-back splits included); compound "
            "upstream Python asserts split per field (session kind, lifecycle "
            "status, stored input, distinct ids, error shapes, final "
            "status/output); the six teardown dispositions ride the cleanup "
            "parent outside the marker caliber"),
        "projected_parsed_total": PARSED_OBLIGATION_COUNT,
        "located_total": PARSED_OBLIGATION_COUNT,
        "located_parents": COARSE_PARENT_COUNT,
        "located_fields": FIELD_OBLIGATION_COUNT,
        "marker_charged_checks": MARKER_CHARGED_CHECKS,
        "marker_to_projection_mapping": (
            "19 charged request-status checks ride the parents and the "
            "per-site field rows; the 4 charged absence checks split into 8 "
            "field obligations; 12 compound upstream asserts never "
            "marker-charged are expanded as density rows; 6 teardown "
            "dispositions ride the cleanup parent"),
        "parsed_unlocated_count": 0,
        "parsed_unlocated": [],
        "upstream_native_assertion_ids": False,
        "poolab_form_difference_note": (
            "the poolab roster recorded the PATCH settings expectation 410 "
            "against an observed 200 on its sim deployment; this projection "
            "freezes the router.rs-reviewed station form (410 "
            "service_policy_retired) and never borrows the poolab form"),
        "honesty": (
            "no obligation exists without a service_policy_story.py line "
            "anchor and upstream quote; token/register extraction lines, id "
            "selections and the marker print itself are disclosed as "
            "not-charged probes, never as coverage"),
    }
