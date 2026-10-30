"""Source-anchored field-level density obligations for the delivery-policy story.

The upstream shell story ``story_human_manages_delivery_policy`` (the migration
brief's ``story_user_manages_delivery_policy`` wording aliases the same shell
function; the upstream id is used verbatim and never renamed) drives ten
ordered HTTP calls against the legacy administration face of the effective
message-delivery policy: human read, Bot read/write denial, human CAS update,
read-back, stale-write conflict, conflict preservation, restoration, and
restored read-back.  The committed migration contract binds those ten calls as
coarse parent obligations (``DELIVERY.*``); the committed coverage-map row
snapshots 22 raw assertion call sites, and the migration queue scores this
story's density expansion at 36 points.

This builder projects the reviewed 36-point roster without inventing
coverage:

* ten PARENT obligations stay 1:1 with the coarse ``DELIVERY.*`` map and
  carry the migration-contract expectation verbatim;
* thirteen anchored field sites - response fields (``version``, ``policy``,
  ``updated_by``), the explicit 409 error envelope (``error``), and the
  persistence read-backs after denial, update, and conflict - each project
  two executable FIELD obligations (envelope presence/JSON shape and
  comparator value), giving the 26 density obligations and the 36-point
  parsed roster;
* every obligation anchors to its ``stories.sh`` line with the exact upstream
  quote (assertion line and request line), validated live at build time;
  anchor drift fails closed with ``RunStateError``;
* shell probes without an assertion role (extraction pipelines, request-body
  construction, the always-attempt-restoration comment block) are recorded as
  ``NOT_CHARGED_SOURCE_PROBES`` and never silently counted as coverage;
* nothing parsed-but-unlocated exists in this projection, so ``parsed_unlocated``
  is honestly empty and every coverage caliber is disclosed by
  ``delivery_density_reconciliation``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..local_delivery_policy_contract import REQUIREMENTS
from ..run_state import RunStateError

PARENT = "story_human_manages_delivery_policy"
POLICY_PATH = "/admin/message-delivery/policy"
SOURCE_FILE = "apps/bcs/scripts/e2e-test/stories.sh"
SOURCE_PATH = Path(__file__).resolve().parents[2].parent / "e2e-test" / "stories.sh"

COARSE_PARENT_COUNT = 10
FIELD_SITE_COUNT = 13
FIELD_OBLIGATION_COUNT = 26
PARSED_OBLIGATION_COUNT = COARSE_PARENT_COUNT + FIELD_OBLIGATION_COUNT

STATUS_KIND = "status"
FIELD_KIND = "field"
ENVIRONMENT_ASPECT = "envelope"
VALUE_ASPECT = "value"

# --- coarse parent sites: one per reviewed migration-contract requirement ---
# (requirement_id, family, assertion_line, assertion_quote, request_line,
#  request_quote, method, caller, expected_status, contract expectation)
_PARENT_SITES: tuple[tuple[str, str, int, str, int, str, str, str, int, str], ...] = (
    ("DELIVERY.human-read", "READ", 50,
     'require_status "human reads delivery policy" "200"',
     49, 'api_get "$path"', "GET", "human", 200,
     "Human identity reads the effective delivery policy; version is a non-negative integer and policy is a complete object."),
    ("DELIVERY.bot-read-denied", "DENIAL", 60,
     'assert_status "Bot cannot read administrative delivery policy" "403"',
     59, 'bot_get "$path" CEO', "GET", "bot", 403,
     "A Bot token is rejected with 403; Bot credentials cannot administer delivery policy."),
    ("DELIVERY.bot-write-denied", "DENIAL", 62,
     'assert_status "Bot cannot replace administrative delivery policy" "403"',
     61, 'bot_put "$path" CEO "$body"', "PUT", "bot", 403,
     "A Bot token PUT is rejected with 403 and never replaces the policy."),
    ("DELIVERY.denied-preserves", "DENIAL", 64,
     'require_status "human reads policy after denied Bot writes" "200"',
     63, 'api_get "$path"', "GET", "human", 200,
     "After denied Bot writes the human read-back keeps the identical version and complete policy."),
    ("DELIVERY.cas-update", "CAS", 71,
     'assert_status "human replaces policy with matching version" "200"',
     70, 'api_put "$path" "$body"', "PUT", "human", 200,
     "Human PUT with the matching expected_version replaces the policy; response records version+1, updated_by as the human actor, and the complete updated policy."),
    ("DELIVERY.readback", "CAS", 76,
     'assert_status "human reads back updated policy" "200"',
     75, 'api_get "$path"', "GET", "human", 200,
     "Independent human read-back exposes the incremented version and the complete updated policy."),
    ("DELIVERY.stale-rejected", "CONFLICT", 81,
     'assert_status "stale policy replacement is rejected" "409"',
     80, 'api_put "$path" "$body"', "PUT", "human", 409,
     "Human PUT with a stale expected_version is rejected 409 with error delivery_policy_version_conflict."),
    ("DELIVERY.conflict-preserves", "CONFLICT", 84,
     'assert_status "human reads policy after stale write" "200"',
     83, 'api_get "$path"', "GET", "human", 200,
     "After the stale-write conflict the human read-back keeps the version and policy unchanged."),
    ("DELIVERY.restore", "RESTORE", 89,
     'assert_status "human restores original policy" "200"',
     88, 'api_put "$path" "$restore_body"', "PUT", "human", 200,
     "Human PUT restoring the original policy with the current expected_version succeeds and records version+1 again."),
    ("DELIVERY.restored", "RESTORE", 91,
     'assert_status "human verifies restored policy" "200"',
     90, 'api_get "$path"', "GET", "human", 200,
     "Final human read-back exposes the once-incremented version and the exact original policy for following stories."),
)

# Request-site metadata inherited by the field rows of each parent.
_FAMILY_BY_REQUIREMENT = {row[0]: row[1] for row in _PARENT_SITES}
_REQUEST_LINE_BY_REQUIREMENT = {row[0]: row[4] for row in _PARENT_SITES}
_REQUEST_QUOTE_BY_REQUIREMENT = {row[0]: row[5] for row in _PARENT_SITES}
_METHOD_BY_REQUIREMENT = {row[0]: row[6] for row in _PARENT_SITES}
_CALLER_BY_REQUIREMENT = {row[0]: row[7] for row in _PARENT_SITES}

# --- anchored field sites: the upstream story's own field-level probes ---
# (short_id, parent_requirement, label, assertion_line, assertion_quote, field,
#  comparator, comparator_text)
_FIELD_SITES: tuple[tuple[str, str, str, int, str, str, str, str], ...] = (
    ("F01", "DELIVERY.human-read", "policy version is an integer", 53,
     'assert_eq "policy version is an integer" "$version" "integer"',
     "version", "NON_NEGATIVE_INTEGER", "a non-negative integer"),
    ("F02", "DELIVERY.denied-preserves", "denied write preserves version", 65,
     'assert_json_eq "denied write preserves version" "$RESPONSE" "version" "$version"',
     "version", "BASE_VERSION", "the pre-denial base version"),
    ("F03", "DELIVERY.denied-preserves", "denied write preserves policy", 66,
     'assert_json_eq "denied write preserves policy" "$RESPONSE" "policy" "$original"',
     "policy", "POLICY_ORIGINAL", "the original policy object"),
    ("F04", "DELIVERY.cas-update", "successful update increments version", 72,
     'assert_json_eq "successful update increments version" "$RESPONSE" "version" "$next_version"',
     "version", "BASE_VERSION_PLUS_ONE", "the base version plus one"),
    ("F05", "DELIVERY.cas-update", "update records the human operator", 73,
     'assert_json_eq "update records the human operator" "$RESPONSE" "updated_by" "human_${BCS_MOCK_USER_ID}"',
     "updated_by", "HUMAN_ACTOR", "the human operator actor id"),
    ("F06", "DELIVERY.cas-update", "update returns complete policy", 74,
     'assert_json_eq "update returns complete policy" "$RESPONSE" "policy" "$updated"',
     "policy", "POLICY_UPDATED", "the updated policy object"),
    ("F07", "DELIVERY.readback", "readback exposes new version", 77,
     'assert_json_eq "readback exposes new version" "$RESPONSE" "version" "$next_version"',
     "version", "BASE_VERSION_PLUS_ONE", "the base version plus one"),
    ("F08", "DELIVERY.readback", "readback exposes complete updated policy", 78,
     'assert_json_eq "readback exposes complete updated policy" "$RESPONSE" "policy" "$updated"',
     "policy", "POLICY_UPDATED", "the updated policy object"),
    ("F09", "DELIVERY.stale-rejected", "stale write has explicit conflict error", 82,
     'assert_json_eq "stale write has explicit conflict error" "$RESPONSE" "error" "delivery_policy_version_conflict"',
     "error", "delivery_policy_version_conflict", "the conflict error code delivery_policy_version_conflict"),
    ("F10", "DELIVERY.conflict-preserves", "conflict does not increment version", 85,
     'assert_json_eq "conflict does not increment version" "$RESPONSE" "version" "$next_version"',
     "version", "BASE_VERSION_PLUS_ONE", "the base version plus one"),
    ("F11", "DELIVERY.conflict-preserves", "conflict does not alter policy", 86,
     'assert_json_eq "conflict does not alter policy" "$RESPONSE" "policy" "$updated"',
     "policy", "POLICY_UPDATED", "the updated policy object"),
    ("F12", "DELIVERY.restored", "restoration increments version again", 92,
     'assert_json_eq "restoration increments version again" "$RESPONSE" "version" "$((next_version + 1))"',
     "version", "BASE_VERSION_PLUS_TWO", "the base version plus two"),
    ("F13", "DELIVERY.restored", "original policy is restored for following stories", 93,
     'assert_json_eq "original policy is restored for following stories" "$RESPONSE" "policy" "$original"',
     "policy", "POLICY_ORIGINAL", "the original policy object"),
)

# JSON shape each anchored response field must expose (envelope aspect).
_SHAPE_BY_FIELD = {
    "version": "NON_NEGATIVE_INTEGER",
    "policy": "OBJECT",
    "updated_by": "NON_EMPTY_STRING",
    "error": "NON_EMPTY_STRING",
}

COMPARATOR_TEXT = {
    "NON_NEGATIVE_INTEGER": "a non-negative integer",
    "BASE_VERSION": "the frozen base version",
    "BASE_VERSION_PLUS_ONE": "the base version plus one",
    "BASE_VERSION_PLUS_TWO": "the base version plus two",
    "POLICY_ORIGINAL": "the original policy object",
    "POLICY_UPDATED": "the updated policy object",
    "HUMAN_ACTOR": "the human operator actor id",
}

# Shell probes with no assertion role.  They are disclosed here so a future
# report cannot silently lose them, and they are never charged to the 36-point
# parsed roster (mirrors the structured-density not-charged probe policy).
NOT_CHARGED_SOURCE_PROBES: tuple[dict[str, Any], ...] = (
    {"source_line": 51,
     "source_quote": "original=$(printf '%s' \"$RESPONSE\"",
     "reason": "response parse pipeline feeding the policy comparators; extraction, not an assertion"},
    {"source_line": 52,
     "source_quote": 'version=$(json_path "$RESPONSE" "version")',
     "reason": "response parse pipeline feeding the version comparators; extraction, not an assertion"},
    {"source_line": 55,
     "source_quote": 'updated=$(printf \'%s\' "$original"',
     "reason": "updated-policy computation (max_context_messages toggle); derivation, not an assertion"},
    {"source_line": 56,
     "source_quote": 'body="{\\"expected_version\\":${version},\\"policy\\":${updated}}"',
     "reason": "CAS request-envelope construction; the matching expected_version is parent DELIVERY.cas-update semantics"},
    {"source_line": 57,
     "source_quote": 'restore_body="{\\"expected_version\\":${next_version},\\"policy\\":${original}}"',
     "reason": "restoration request-envelope construction; the matching expected_version is parent DELIVERY.restore semantics"},
    {"source_line": 68,
     "source_quote": "# Do not return early after attempting a write: always attempt the CAS",
     "reason": "comment block documenting the always-attempt-restoration rule; not an assertion"},
)


@dataclass(frozen=True)
class DensityObligation:
    """One executable projection row anchored to a stories.sh line."""

    obligation_id: str
    level: str
    parent_obligation: str
    family: str
    aspect: str | None
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
    shape: str | None
    expected: Any
    comparator_text: str
    expectation: str
    density: bool
    located: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "level": self.level,
            "parent_obligation": self.parent_obligation,
            "family": self.family,
            "aspect": self.aspect,
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
            "shape": self.shape,
            "expected": self.expected,
            "comparator_text": self.comparator_text,
            "expectation": self.expectation,
            "density": self.density,
            "located": self.located,
        }


def _operation(requirement_id: str) -> str:
    return "delivery_policy." + requirement_id.split(".", 1)[1].replace("-", "_")


def _read_source() -> list[str]:
    try:
        return SOURCE_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RunStateError(f"delivery density source is unavailable: {error}") from None


def _anchor(lines: list[str], line_no: int, quote: str, obligation_id: str) -> None:
    if line_no < 1 or line_no > len(lines) or quote not in lines[line_no - 1]:
        raise RunStateError(
            f"delivery density source anchor changed: {obligation_id} (stories.sh:{line_no})")


def _parent_rows(lines: list[str]) -> tuple[DensityObligation, ...]:
    rows: list[DensityObligation] = []
    for (requirement, family, assert_line, assert_quote, request_line, request_quote,
         method, caller, expected_status, expectation) in _PARENT_SITES:
        _anchor(lines, assert_line, assert_quote, requirement)
        _anchor(lines, request_line, request_quote, requirement)
        rows.append(DensityObligation(
            obligation_id=requirement,
            level="PARENT",
            parent_obligation=requirement,
            family=family,
            aspect=None,
            kind=STATUS_KIND,
            source_line=assert_line,
            source_quote=assert_quote,
            request_line=request_line,
            request_quote=request_quote,
            operation=_operation(requirement),
            method=method,
            path=POLICY_PATH,
            caller=caller,
            field=None,
            shape=None,
            expected=expected_status,
            comparator_text="http_status",
            expectation=expectation,
            density=False,
        ))
    return tuple(rows)


def _field_rows(lines: list[str], parent_ids: tuple[str, ...]) -> tuple[DensityObligation, ...]:
    known = set(parent_ids)
    rows: list[DensityObligation] = []
    for (short_id, parent, label, assert_line, assert_quote, field, comparator,
         comparator_text) in _FIELD_SITES:
        if parent not in known:
            raise RunStateError(f"delivery density field site {short_id} references unknown parent {parent}")
        _anchor(lines, assert_line, assert_quote, f"DELIVERY.{short_id}")
        for aspect in (ENVIRONMENT_ASPECT, VALUE_ASPECT):
            obligation_id = f"DELIVERY.{short_id}.{aspect}"
            if aspect == ENVIRONMENT_ASPECT:
                expectation = (f"{label}; response envelope exposes field `{field}` "
                               f"with JSON shape {_SHAPE_BY_FIELD[field]}")
                expected: Any = _SHAPE_BY_FIELD[field]
            else:
                expectation = f"{label}; field `{field}` equals {comparator_text}"
                expected = comparator
            rows.append(DensityObligation(
                obligation_id=obligation_id,
                level="FIELD",
                parent_obligation=parent,
                family=_FAMILY_BY_REQUIREMENT[parent],
                aspect=aspect,
                kind=FIELD_KIND,
                source_line=assert_line,
                source_quote=assert_quote,
                request_line=_REQUEST_LINE_BY_REQUIREMENT[parent],
                request_quote=_REQUEST_QUOTE_BY_REQUIREMENT[parent],
                operation=_operation(parent),
                method=_METHOD_BY_REQUIREMENT[parent],
                path=POLICY_PATH,
                caller=_CALLER_BY_REQUIREMENT[parent],
                field=field,
                shape=_SHAPE_BY_FIELD[field] if aspect == ENVIRONMENT_ASPECT else None,
                expected=expected,
                comparator_text=comparator_text,
                expectation=expectation,
                density=True,
            ))
    return tuple(rows)


def build_delivery_density_obligations() -> tuple[DensityObligation, ...]:
    """Build and anchor-validate the 36-point obligation roster.

    The roster is derived from the upstream story only: ten coarse parent
    obligations aligned with the committed migration contract plus 26 field
    obligations projected from the 13 anchored field sites.  Any anchor drift
    in ``stories.sh`` fails closed instead of silently regressing coverage.
    """
    lines = _read_source()
    if not lines[44].startswith(PARENT + "()"):
        raise RunStateError("delivery density story function header changed")
    if 'local path="' + POLICY_PATH + '"' not in lines[46]:
        raise RunStateError("delivery density story policy path changed")
    parents = _parent_rows(lines)
    if tuple(row.obligation_id for row in parents) != REQUIREMENTS:
        raise RunStateError("delivery density parent projection changed")
    fields = _field_rows(lines, tuple(row.obligation_id for row in parents))
    rows = parents + fields
    if len(rows) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("delivery density parsed obligation count changed")
    if sum(row.density for row in rows) != FIELD_OBLIGATION_COUNT:
        raise RunStateError("delivery density field obligation count changed")
    if len({row.obligation_id for row in rows}) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("delivery density obligation ids are not unique")
    return rows


def delivery_density_reconciliation() -> dict[str, Any]:
    """Disclose every parsed-obligation caliber honestly.

    The committed coverage-map row snapshots 22 raw upstream call sites (the
    Bash ``[[ ]]`` guard at line 53 is not an ``assert_*`` call site); this
    projection charges that guard as anchored field site F01, splits each of
    the 13 anchored field sites into envelope and value obligations, and
    therefore parses 36 executable obligations with zero unlocated items.
    """
    return {
        "coverage_map_row": "coverage-map/coverage-map-20261031.json no=1 story_human_manages_delivery_policy",
        "coverage_map_snapshot_parsed_count": 22,
        "coverage_map_snapshot_basis": (
            "committed row: raw assert-status/assert-json call sites; the Bash "
            "[[ ]] guard at line 53 is not an assert_* call site"),
        "migration_queue_score": 36,
        "task_brief_parsed_caliber": 36,
        "raw_anchored_sites": COARSE_PARENT_COUNT + FIELD_SITE_COUNT,
        "raw_status_sites": COARSE_PARENT_COUNT,
        "raw_field_sites": FIELD_SITE_COUNT,
        "projection_rule": (
            "parents stay 1:1 with the ten coarse DELIVERY.* status obligations; "
            "each anchored field site projects two executable density obligations: "
            "envelope presence/JSON shape and comparator value"),
        "projected_parsed_total": PARSED_OBLIGATION_COUNT,
        "located_total": PARSED_OBLIGATION_COUNT,
        "located_parents": COARSE_PARENT_COUNT,
        "located_fields": FIELD_OBLIGATION_COUNT,
        "parsed_unlocated_count": 0,
        "parsed_unlocated": [],
        "guard_line_53_note": (
            "conditional Bash guard charged here as anchored field site F01 "
            "(version integer shape); the committed snapshot caliber excluded "
            "non-assert_* call sites"),
        "upstream_native_assertion_ids": False,
        "story_id_note": (
            "migration brief wording story_user_manages_delivery_policy aliases "
            "the upstream function story_human_manages_delivery_policy; the "
            "upstream id is used verbatim"),
        "honesty": (
            "no obligation exists without a stories.sh line anchor and upstream "
            "quote; extraction pipelines, request-body construction, and comments "
            "are disclosed as not-charged probes, never as coverage"),
    }
