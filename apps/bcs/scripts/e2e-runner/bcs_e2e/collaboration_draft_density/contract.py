"""Source-anchored density contract for Draft delivery Wave-W2-#12.

The existing Draft delivery family has eight coarse parent obligations.  This
projection adds twenty-four field-level rows from the frozen shell story, for
a 32-point parsed caliber with no residual count gap.  The source file is
read-only: its SHA-256 and every exact line-range quote are checked before the
plan can be used.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..run_state import RunStateError

SCHEMA_VERSION = 1
PARENT = "story_collaboration_draft_delivery"
DENSITY_CONTRACT = "collaboration-draft-density-v1"
SOURCE_FILE = "apps/bcs/scripts/e2e-test/collaboration_draft_story.sh"
SOURCE_PATH = Path(__file__).resolve().parents[2].parent / "e2e-test" / "collaboration_draft_story.sh"
SOURCE_SHA256 = "7c081dfb84b04549572c173f5da51125f5ff5475648ce471664c83cf3c77f4d2"
STORY_LINE_SPAN = (17, 92)
PARENT_OBLIGATION_COUNT = 8
DENSITY_OBLIGATION_COUNT = 24
PARSED_OBLIGATION_COUNT = 32
RESIDUAL_COUNT_GAP = 0
HUMAN_COOKIE_ENV = "BCS_E2E_HUMAN_COOKIE"
DRIVER_TOKEN_ENV = "BCS_E2E_DRIVER_TOKEN"

# The eight existing coarse obligations are retained as the parent layer.
PARENT_IDS = (
    "DRAFT-DELIVERY.offline-preview",
    "DRAFT-DELIVERY.create-first-revision",
    "DRAFT-DELIVERY.owner-metadata",
    "DRAFT-DELIVERY.shared-read-validation",
    "DRAFT-DELIVERY.fixed-revision-cli",
    "DRAFT-DELIVERY.cas-update-history",
    "DRAFT-DELIVERY.revoke-delete-lifecycle",
    "DRAFT-DELIVERY.journey-completes",
)


@dataclass(frozen=True)
class Obligation:
    """One parent or field obligation with an exact upstream source anchor."""

    obligation_id: str
    level: str
    method: str
    line_start: int
    line_end: int
    quote: str
    parent_obligation: str
    expectation: str
    operation: str
    field: str
    comparator: str
    expected: Any = None

    @property
    def source_file(self) -> str:
        return SOURCE_FILE

    @property
    def source_line(self) -> int:
        return self.line_start

    @property
    def source_quote(self) -> str:
        return self.quote

    @property
    def located(self) -> bool:
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.obligation_id,
            "level": self.level,
            "method": self.method,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "quote": self.quote,
            "source_file": SOURCE_FILE,
            "parent_obligation": self.parent_obligation,
            "expectation": self.expectation,
            "operation": self.operation,
            "field": self.field,
            "comparator": self.comparator,
            "expected": self.expected,
            "located": True,
        }


def _row(*, obligation_id: str, level: str, method: str, line_start: int,
         line_end: int, quote: str, parent: str, expectation: str,
         operation: str, field: str, comparator: str, expected: Any = None) -> Obligation:
    return Obligation(obligation_id, level, method, line_start, line_end, quote,
                      parent, expectation, operation, field, comparator, expected)


# Parent anchors are intentionally coarse stage anchors.  The 24 density rows
# below are the atomic observations which close parsed 32 - parent 8.
_PARENT_ROWS = (
    _row(obligation_id=PARENT_IDS[0], level="PARENT", method="offline_preview",
         line_start=36, line_end=42,
         quote="\n".join([
             '    _cli_story_run "existing offline preview still emits a schema 1 snapshot" PM collaboration preview \\',
             '        "$directory/original.yaml" || return 1',
             '    assert_json_eq "offline preview retains schema 1" "$BCS_CLI_STDOUT" snapshot.schemaVersion 1',
             '    printf \'%s\' "$BCS_CLI_STDOUT" | python3 -c \\',
             '        \'import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(json.load(sys.stdin)["snapshot"]["definitionYaml"].encode())\' \\',
             '        "$directory/offline.yaml" || return 1',
             '    assert_ok "offline preview preserves the incident source" cmp "$directory/original.yaml" "$directory/offline.yaml"',
         ]), parent=PARENT_IDS[0], expectation="Offline preview retains schema 1 and exact incident bytes.",
         operation="draft.offline_preview", field="snapshot", comparator="stage"),
    _row(obligation_id=PARENT_IDS[1], level="PARENT", method="create_first_revision",
         line_start=43, line_end=48,
         quote="\n".join([
             '    _cli_story_run "author uploads the incident file" PM collaboration draft create \\',
             '        "$directory/original.yaml" --draft-id "$id" --title "Draft delivery regression" || return 1',
             '    assert_json_eq "saved source has 2174 bytes" "$BCS_CLI_STDOUT" size_bytes 2174',
             '    assert_json_eq "saved source validates" "$BCS_CLI_STDOUT" validation.validation.valid true',
             '    assert_json_eq "first revision is immutable" "$BCS_CLI_STDOUT" revision 1',
             '    [[ "$BCS_CLI_STDOUT" != *readToken* ]] || return 1',
         ]), parent=PARENT_IDS[1], expectation="Create stores the incident source as immutable revision one without token leakage.",
         operation="draft.create", field="revision", comparator="stage"),
    _row(obligation_id=PARENT_IDS[2], level="PARENT", method="owner_metadata",
         line_start=52, line_end=54,
         quote="\n".join([
             '    api_request_headers GET "/collaboration/drafts/$id" \'\' "Authorization: Bearer $owner_token"',
             '    require_status "author reads Draft metadata" 200 || return 1',
             '    assert_json_eq "metadata points to revision one" "$RESPONSE" head_revision 1',
         ]), parent=PARENT_IDS[2], expectation="The owner reads metadata with head revision one.",
         operation="draft.owner_metadata", field="head_revision", comparator="stage"),
    _row(obligation_id=PARENT_IDS[3], level="PARENT", method="shared_read_validation",
         line_start=55, line_end=64,
         quote="\n".join([
             '    api_request_headers GET "/collaboration/drafts/$id/revisions/1" \'\'',
             '    require_status "a card holder reads without Bot credentials or Session membership" 200 || return 1',
             '    assert_json_eq "shared read preserves incident hash" "$RESPONSE" content_hash \\',
             '        761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7',
             '    assert_contains "shared source is not cached" "$RESPONSE_HEADERS" no-store',
             '    api_request_headers POST "/collaboration/drafts/$id/revisions/1/validate" \'\'',
             '    require_status "card holder validates the stored source" 200 || return 1',
             '    assert_json_eq "shared validation binds revision one" "$RESPONSE" revision 1',
             '    api_request_headers DELETE "/collaboration/drafts/$id" \'\'',
             '    require_status "public reader cannot delete" 401 || return 1',
         ]), parent=PARENT_IDS[3], expectation="Tokenless shared read and validation work, while public delete is unauthorized.",
         operation="draft.shared_read_validation", field="response", comparator="stage"),
    _row(obligation_id=PARENT_IDS[4], level="PARENT", method="fixed_revision_cli",
         line_start=66, line_end=69,
         quote="\n".join([
             '    _cli_story_run "author downloads exact original bytes" PM collaboration draft get "$id" \\',
             '        --revision 1 --output "$directory/download.yaml" || return 1',
             '    assert_ok "download matches incident file byte for byte" cmp "$directory/original.yaml" "$directory/download.yaml"',
             '    _cli_story_run "author validates the fixed revision" PM collaboration draft validate "$id" --revision 1 || return 1',
         ]), parent=PARENT_IDS[4], expectation="CLI get and validate operate on the fixed original revision.",
         operation="draft.fixed_revision_cli", field="revision", comparator="stage"),
    _row(obligation_id=PARENT_IDS[5], level="PARENT", method="cas_update_history",
         line_start=70, line_end=78,
         quote="\n".join([
             '    _cli_story_run "author appends an explicit new revision" PM collaboration draft update "$id" \\',
             '        "$directory/updated.yaml" --expected-revision 1 || return 1',
             '    assert_json_eq "update advances exactly once" "$BCS_CLI_STDOUT" revision 2',
             '    _cli_story_run "author reissues the historical card" PM collaboration draft preview "$id" --revision 1 || return 1',
             '    assert_json_eq "historical preview still returns the original bytes" "$BCS_CLI_STDOUT" size_bytes 2174',
             '    assert_json_eq "reissuing a card preserves its fixed version" "$BCS_CLI_STDOUT" reference.revision 1',
             '    api_request_headers POST "/collaboration/drafts/$id/revisions" \\',
             '        \'{"expected_revision":1,"definition_yaml":"different"}\' "Authorization: Bearer $owner_token"',
             '    require_status "stale writer cannot replace the newer revision" 409 || return 1',
         ]), parent=PARENT_IDS[5], expectation="CAS advances once, historical preview stays fixed, and stale writes are rejected.",
         operation="draft.cas_update_history", field="revision", comparator="stage"),
    _row(obligation_id=PARENT_IDS[6], level="PARENT", method="revoke_delete_lifecycle",
         line_start=80, line_end=91,
         quote="\n".join([
             '    _cli_story_run "author revokes only the second revision" PM collaboration draft revoke "$id" --revision 2 || return 1',
             '    _cli_story_run "repeated revocation is idempotent" PM collaboration draft revoke "$id" --revision 2 || return 1',
             '    api_request_headers GET "/collaboration/drafts/$id/revisions/2" \'\'',
             '    require_status "revoked card becomes unavailable" 404 || return 1',
             '    api_request_headers GET "/collaboration/drafts/$id/revisions/1" \'\'',
             '    require_status "old shared revision remains readable" 200 || return 1',
             '    _cli_story_run "author deletes the disposable Draft" PM collaboration draft delete "$id" || return 1',
             '    api_request_headers GET "/collaboration/drafts/$id/revisions/1" \'\'',
             '    require_status "deletion revokes every historical card" 404 || return 1',
             '    api_request_headers PUT "/collaboration/drafts/$id" \\',
             '        \'{"definition_yaml":"late request"}\' "Authorization: Bearer $owner_token"',
             '    require_status "late create cannot revive a deleted Draft" 404 || return 1',
         ]), parent=PARENT_IDS[6], expectation="Revision revoke and Draft deletion close every historical access path.",
         operation="draft.revoke_delete", field="lifecycle", comparator="stage"),
    _row(obligation_id=PARENT_IDS[7], level="PARENT", method="journey_completes",
         line_start=23, line_end=23,
         quote='    assert_eq "Draft delivery journey completes" "$result" "0"',
         parent=PARENT_IDS[7], expectation="The upstream story wrapper completes successfully.",
         operation="draft.story", field="result", comparator="status", expected=0),
)

_DENSITY_ROWS = (
    _row(obligation_id="DRAFT-DENSITY.D01", level="DENSITY", method="offline_schema1",
         line_start=38, line_end=38, quote='    assert_json_eq "offline preview retains schema 1" "$BCS_CLI_STDOUT" snapshot.schemaVersion 1',
         parent=PARENT_IDS[0], expectation="Offline snapshot schemaVersion is 1.", operation="offline", field="schema_version", comparator="equals", expected=1),
    _row(obligation_id="DRAFT-DENSITY.D02", level="DENSITY", method="offline_source_bytes",
         line_start=42, line_end=42, quote='    assert_ok "offline preview preserves the incident source" cmp "$directory/original.yaml" "$directory/offline.yaml"',
         parent=PARENT_IDS[0], expectation="Offline preview bytes compare equal to the incident source.", operation="offline", field="bytes_equal", comparator="equals", expected=True),
    _row(obligation_id="DRAFT-DENSITY.D03", level="DENSITY", method="create_size",
         line_start=45, line_end=45, quote='    assert_json_eq "saved source has 2174 bytes" "$BCS_CLI_STDOUT" size_bytes 2174',
         parent=PARENT_IDS[1], expectation="Created source is 2174 bytes.", operation="create", field="size_bytes", comparator="equals", expected=2174),
    _row(obligation_id="DRAFT-DENSITY.D04", level="DENSITY", method="create_validation",
         line_start=46, line_end=46, quote='    assert_json_eq "saved source validates" "$BCS_CLI_STDOUT" validation.validation.valid true',
         parent=PARENT_IDS[1], expectation="Created source validation.valid is true.", operation="create", field="validation_valid", comparator="equals", expected=True),
    _row(obligation_id="DRAFT-DENSITY.D05", level="DENSITY", method="create_revision_one",
         line_start=47, line_end=47, quote='    assert_json_eq "first revision is immutable" "$BCS_CLI_STDOUT" revision 1',
         parent=PARENT_IDS[1], expectation="First revision is revision 1.", operation="create", field="revision", comparator="equals", expected=1),
    _row(obligation_id="DRAFT-DENSITY.D06", level="DENSITY", method="create_no_read_token",
         line_start=48, line_end=48, quote='    [[ "$BCS_CLI_STDOUT" != *readToken* ]] || return 1',
         parent=PARENT_IDS[1], expectation="Create output does not contain readToken.", operation="create", field="read_token_present", comparator="equals", expected=False),
    _row(obligation_id="DRAFT-DENSITY.D07", level="DENSITY", method="owner_head_revision",
         line_start=54, line_end=54, quote='    assert_json_eq "metadata points to revision one" "$RESPONSE" head_revision 1',
         parent=PARENT_IDS[2], expectation="Owner metadata reports head_revision 1.", operation="owner_metadata", field="head_revision", comparator="equals", expected=1),
    _row(obligation_id="DRAFT-DENSITY.D08", level="DENSITY", method="shared_read_status",
         line_start=55, line_end=56, quote="\n".join([
             '    api_request_headers GET "/collaboration/drafts/$id/revisions/1" \'\'',
             '    require_status "a card holder reads without Bot credentials or Session membership" 200 || return 1',
         ]), parent=PARENT_IDS[3], expectation="Card holder reads revision one with HTTP 200 and no Bot or Session membership.", operation="shared_read", field="status", comparator="equals", expected=200),
    _row(obligation_id="DRAFT-DENSITY.D09", level="DENSITY", method="shared_hash",
         line_start=57, line_end=58, quote="\n".join([
             '    assert_json_eq "shared read preserves incident hash" "$RESPONSE" content_hash \\',
             '        761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7',
         ]), parent=PARENT_IDS[3], expectation="Shared read preserves the incident content hash.", operation="shared_read", field="content_hash", comparator="equals", expected="761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7"),
    _row(obligation_id="DRAFT-DENSITY.D10", level="DENSITY", method="shared_no_store",
         line_start=59, line_end=59, quote='    assert_contains "shared source is not cached" "$RESPONSE_HEADERS" no-store',
         parent=PARENT_IDS[3], expectation="Shared read carries no-store cache control.", operation="shared_read", field="cache_control", comparator="contains", expected="no-store"),
    _row(obligation_id="DRAFT-DENSITY.D11", level="DENSITY", method="shared_validate_status",
         line_start=60, line_end=61, quote="\n".join([
             '    api_request_headers POST "/collaboration/drafts/$id/revisions/1/validate" \'\'',
             '    require_status "card holder validates the stored source" 200 || return 1',
         ]), parent=PARENT_IDS[3], expectation="Card holder validation returns HTTP 200.", operation="shared_validate", field="status", comparator="equals", expected=200),
    _row(obligation_id="DRAFT-DENSITY.D12", level="DENSITY", method="shared_validate_revision",
         line_start=62, line_end=62, quote='    assert_json_eq "shared validation binds revision one" "$RESPONSE" revision 1',
         parent=PARENT_IDS[3], expectation="Shared validation binds revision one.", operation="shared_validate", field="revision", comparator="equals", expected=1),
    _row(obligation_id="DRAFT-DENSITY.D13", level="DENSITY", method="public_delete_unauthorized",
         line_start=63, line_end=64, quote="\n".join([
             '    api_request_headers DELETE "/collaboration/drafts/$id" \'\'',
             '    require_status "public reader cannot delete" 401 || return 1',
         ]), parent=PARENT_IDS[3], expectation="Public reader delete returns HTTP 401.", operation="public_delete", field="status", comparator="equals", expected=401),
    _row(obligation_id="DRAFT-DENSITY.D14", level="DENSITY", method="download_exact_bytes",
         line_start=68, line_end=68, quote='    assert_ok "download matches incident file byte for byte" cmp "$directory/original.yaml" "$directory/download.yaml"',
         parent=PARENT_IDS[4], expectation="CLI get output matches the incident bytes exactly.", operation="cli_get", field="bytes_equal", comparator="equals", expected=True),
    _row(obligation_id="DRAFT-DENSITY.D15", level="DENSITY", method="validate_fixed_revision",
         line_start=69, line_end=69, quote='    _cli_story_run "author validates the fixed revision" PM collaboration draft validate "$id" --revision 1 || return 1',
         parent=PARENT_IDS[4], expectation="CLI validates fixed revision one successfully.", operation="cli_validate", field="exit_code", comparator="equals", expected=0),
    _row(obligation_id="DRAFT-DENSITY.D16", level="DENSITY", method="update_revision_two",
         line_start=72, line_end=72, quote='    assert_json_eq "update advances exactly once" "$BCS_CLI_STDOUT" revision 2',
         parent=PARENT_IDS[5], expectation="Expected-revision update advances to revision two exactly once.", operation="update", field="revision", comparator="equals", expected=2),
    _row(obligation_id="DRAFT-DENSITY.D17", level="DENSITY", method="historical_preview_bytes",
         line_start=74, line_end=74, quote='    assert_json_eq "historical preview still returns the original bytes" "$BCS_CLI_STDOUT" size_bytes 2174',
         parent=PARENT_IDS[5], expectation="Revision-one preview remains 2174 bytes.", operation="historical_preview", field="size_bytes", comparator="equals", expected=2174),
    _row(obligation_id="DRAFT-DENSITY.D18", level="DENSITY", method="historical_preview_revision",
         line_start=75, line_end=75, quote='    assert_json_eq "reissuing a card preserves its fixed version" "$BCS_CLI_STDOUT" reference.revision 1',
         parent=PARENT_IDS[5], expectation="Historical preview reference remains pinned to revision one.", operation="historical_preview", field="reference_revision", comparator="equals", expected=1),
    _row(obligation_id="DRAFT-DENSITY.D19", level="DENSITY", method="stale_writer_conflict",
         line_start=76, line_end=78, quote="\n".join([
             '    api_request_headers POST "/collaboration/drafts/$id/revisions" \\',
             '        \'{"expected_revision":1,"definition_yaml":"different"}\' "Authorization: Bearer $owner_token"',
             '    require_status "stale writer cannot replace the newer revision" 409 || return 1',
         ]), parent=PARENT_IDS[5], expectation="Stale expected_revision write returns HTTP 409.", operation="stale_write", field="status", comparator="equals", expected=409),
    _row(obligation_id="DRAFT-DENSITY.D20", level="DENSITY", method="revoke_idempotent",
         line_start=81, line_end=81, quote='    _cli_story_run "repeated revocation is idempotent" PM collaboration draft revoke "$id" --revision 2 || return 1',
         parent=PARENT_IDS[6], expectation="Repeated revoke of revision two succeeds idempotently.", operation="revoke_repeat", field="exit_code", comparator="equals", expected=0),
    _row(obligation_id="DRAFT-DENSITY.D21", level="DENSITY", method="revoked_card_404",
         line_start=82, line_end=83, quote="\n".join([
             '    api_request_headers GET "/collaboration/drafts/$id/revisions/2" \'\'',
             '    require_status "revoked card becomes unavailable" 404 || return 1',
         ]), parent=PARENT_IDS[6], expectation="Revoked revision two is unavailable with HTTP 404.", operation="revoked_read", field="status", comparator="equals", expected=404),
    _row(obligation_id="DRAFT-DENSITY.D22", level="DENSITY", method="old_revision_readable",
         line_start=84, line_end=85, quote="\n".join([
             '    api_request_headers GET "/collaboration/drafts/$id/revisions/1" \'\'',
             '    require_status "old shared revision remains readable" 200 || return 1',
         ]), parent=PARENT_IDS[6], expectation="Revision one remains readable after revision two revoke.", operation="old_revision_read", field="status", comparator="equals", expected=200),
    _row(obligation_id="DRAFT-DENSITY.D23", level="DENSITY", method="delete_revokes_history",
         line_start=87, line_end=88, quote="\n".join([
             '    api_request_headers GET "/collaboration/drafts/$id/revisions/1" \'\'',
             '    require_status "deletion revokes every historical card" 404 || return 1',
         ]), parent=PARENT_IDS[6], expectation="Draft deletion revokes the remaining historical card with HTTP 404.", operation="deleted_read", field="status", comparator="equals", expected=404),
    _row(obligation_id="DRAFT-DENSITY.D24", level="DENSITY", method="late_put_404",
         line_start=89, line_end=91, quote="\n".join([
             '    api_request_headers PUT "/collaboration/drafts/$id" \\',
             '        \'{"definition_yaml":"late request"}\' "Authorization: Bearer $owner_token"',
             '    require_status "late create cannot revive a deleted Draft" 404 || return 1',
         ]), parent=PARENT_IDS[6], expectation="Late PUT cannot revive a deleted Draft and returns HTTP 404.", operation="late_put", field="status", comparator="equals", expected=404),
)


def _validate_source(rows: tuple[Obligation, ...]) -> None:
    if SOURCE_PATH.is_symlink() or not SOURCE_PATH.is_file():
        raise RunStateError("collaboration-draft density source is unavailable")
    raw = SOURCE_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise RunStateError("collaboration-draft density source SHA-256 drifted")
    lines = raw.decode("utf-8").splitlines()
    for row in rows:
        if not 1 <= row.line_start <= row.line_end <= len(lines):
            raise RunStateError(f"collaboration-draft density anchor range invalid: {row.obligation_id}")
        if "\n".join(lines[row.line_start - 1:row.line_end]) != row.quote:
            raise RunStateError(f"collaboration-draft density anchor drifted: {row.obligation_id}")


def obligations() -> tuple[Obligation, ...]:
    rows = _PARENT_ROWS + _DENSITY_ROWS
    if len(_PARENT_ROWS) != PARENT_OBLIGATION_COUNT or len(_DENSITY_ROWS) != DENSITY_OBLIGATION_COUNT:
        raise RunStateError("collaboration-draft density layer count changed")
    if len(rows) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("collaboration-draft parsed count changed")
    if [row.obligation_id for row in _PARENT_ROWS] != list(PARENT_IDS):
        raise RunStateError("collaboration-draft parent obligation set changed")
    if len({row.obligation_id for row in rows}) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("collaboration-draft obligation IDs are not unique")
    if any(row.parent_obligation not in PARENT_IDS for row in rows):
        raise RunStateError("collaboration-draft parent projection changed")
    _validate_source(rows)
    return rows


def density_plan() -> dict[str, Any]:
    rows = obligations()
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": DENSITY_CONTRACT,
        "parent": PARENT,
        "source_file": SOURCE_FILE,
        "source_sha256": SOURCE_SHA256,
        "story_line_span": list(STORY_LINE_SPAN),
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "delta": DENSITY_OBLIGATION_COUNT,
        "projection_rule": "8 existing coarse Draft delivery parents plus 24 exact source-anchored field atoms; the 302 identity redirect remains a named preprod product defect and is never silently adapted.",
        "obligations": [row.as_dict() for row in rows],
        "parsed_unlocated_count": 0,
        "parsed_unlocated": [],
        "not_charged_source_probes": [],
        "source_read_only": True,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_references": [HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV],
    }
