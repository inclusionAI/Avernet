"""Frozen 42-obligation density roster for the Bot-authority user journey.

The upstream source is the single shell story
``apps/bcs/scripts/e2e-test/authority.sh`` (content sha256 frozen below; its
git tip revision is recorded for provenance only, this module never shells
out). The story function ``story_authority_user_journey`` occupies lines
93-275 and contains exactly 42 shell assertions: 20 ``require_status`` case
gates plus 22 value assertions (``assert_json_eq``,
``assert_json_array_contains``, ``assert_contains`` and one window-semantics
``assert_ok``). This roster obligations all 42 one-by-one, each anchored
verbatim to its own assertion statement and to the upstream HTTP call that
produced the response under test.

Parent projection: the 42 shell obligations project onto 20 upstream call
groups (one per replayed product-HTTP call envelope). Unlike the structured
and group-context density rosters there is no command-envelope layer: the
upstream story is one flat shell case, so the call group is the truthful
parent unit and the legacy 16-row journal map is explicitly out of scope.

Every line of the story function is charged exactly once: the 42 assertion
spans, the 20 upstream call spans and 30 disclosed projection exclusions
tile lines 93-275 without gaps or overlaps; ``_validate_tiling`` fails
closed otherwise. SHA drift, anchor drift, roster drift or tiling drift
raises ``RunStateError`` before any replay transport exists.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

from ..run_state import RunStateError

SCHEMA_VERSION = 1
PARENT = "story_authority_user_journey"
CASE_ID = "DUAL-AUTHORITY-DENSITY-01"
CONTRACT = "authority-density-v1"
SOURCE_FILE = "apps/bcs/scripts/e2e-test/authority.sh"
SOURCE_PATH = Path(__file__).resolve().parents[2].parent / "e2e-test" / "authority.sh"
SOURCE_REVISION = "c6f00120949078f14cb0d3602fb8301f2ccb1803"
SOURCE_SHA256 = "62c1f09fd74fc881edb191a827c9e2d91e11afd38980fd83c5333828371dd69a"
STORY_FUNCTION = "story_authority_user_journey"
STORY_LINE_START = 93
STORY_LINE_END = 275
OBLIGATION_PREFIX = "AUTHORITY-JOURNEY."
GROUP_PREFIX = "AUTHORITY-JOURNEY-GROUP."
PARSED_OBLIGATION_COUNT = 42
CALL_GROUP_COUNT = 20
SHELL_STATUS_ASSERTIONS = 20
SHELL_VALUE_ASSERTIONS = 22
RESIDUAL_COUNT_GAP = 0
BOOTSTRAP_HTTP_CALLS = 4
BUSINESS_HTTP_CALLS = 20
HTTP_CALL_COUNT = 24
INTERNAL_PREFIX = "/api/v1/collaboration"
PUBLIC_PREFIX = "/openapi/v1/collaboration"
OWNER_USER_ID = "human_owner_auth_density_1"
TARGET_USER_ID = "002"
MINE_USER_ID = "137"
OWNER_BOT_ID = "bot_a1b2c3d4"
TARGET_BOT_ID = "bot_e5f6a7b8"
NEVER_USER_ID = "never-seen-0a0b0c0d0e0f"
TRANSFER_WINDOW_MAX_RENEWAL_MS = 300000
INITIATED_EXPIRES_AT = 1893456000000
REFRESHED_EXPIRES_AT = INITIATED_EXPIRES_AT + 60000
ENVIRONMENT_POSTURE = {
    "local": "OWNED_LOCAL_OFFLINE_REPLAY",
    "preprod": "REMOTE_ADMISSION_GATED_READ_ONLY",
}
ASSERTION_KINDS = (
    "require_status",
    "assert_json_eq",
    "assert_json_array_contains",
    "assert_contains",
    "assert_ok",
)
BUSINESS_ENVELOPE_KEYS = ("code", "message", "request_id", "data")
_BINDING_TOKENS = ("OWNER_BOT_ID", "OWNER_USER_ID", "TARGET_USER_ID", "RENEWAL_WINDOW_MS")


@dataclass(frozen=True)
class Obligation:
    """One parsed upstream shell assertion plus its upstream call anchor."""

    obligation_id: str
    parent_obligation: str
    family: str
    level: str
    method: str
    assertion: str
    line_start: int
    line_end: int
    quote: str
    call_line_start: int
    call_line_end: int
    call_quote: str
    path_template: str
    status: int
    actor: str
    query: tuple[str, str] | None
    operation: str
    field: str
    expected: Any
    density: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "parent_obligation": self.parent_obligation,
            "family": self.family,
            "level": self.level,
            "method": self.method,
            "assertion": self.assertion,
            "source": {
                "file": SOURCE_FILE,
                "line_start": self.line_start,
                "line_end": self.line_end,
                "quote": self.quote,
            },
            "call": {
                "method": self.method,
                "path_template": self.path_template,
                "status": self.status,
                "actor": self.actor,
                "query": (dict([self.query]) if self.query else None),
                "line_start": self.call_line_start,
                "line_end": self.call_line_end,
                "quote": self.call_quote,
            },
            "operation": self.operation,
            "field": self.field,
            "expected": self.expected,
            "density": self.density,
            "located": True,
        }


# (slug, call group, operation, assertion kind, method, path template, frozen
# status, actor, query, json field, expected binding, assertion span, upstream
# call span, verbatim assertion quote, verbatim call quote). Spans and quotes
# are validated verbatim against the frozen source on every plan build.
_ROWS = (
    ('owner-ownership-read-status', 'ownership-read', 'ownership.owner.read', 'require_status', 'GET',
     '/api/v1/collaboration/bots/{bot_id}/ownership', 200, 'owner', None, 'http_status', None, (139, 139), (138, 138),
     '    require_status "owner reads the collaboration ownership view" "200" || return',
     '    _au_request GET "${prefix}/bots/${owner_bot}/ownership" "$principal_a"'),
    ('ownership-keeps-bot-id', 'ownership-read', 'ownership.owner.read', 'assert_json_eq', 'GET',
     '/api/v1/collaboration/bots/{bot_id}/ownership', 200, 'owner', None, 'data.bot_id', 'OWNER_BOT_ID', (140, 140),
     (138, 138), '    assert_json_eq "ownership keeps the bot id" "$RESPONSE" "data.bot_id" "$owner_bot"',
     '    _au_request GET "${prefix}/bots/${owner_bot}/ownership" "$principal_a"'),
    ('ownership-identifies-registered-owner', 'ownership-read', 'ownership.owner.read', 'assert_json_eq', 'GET',
     '/api/v1/collaboration/bots/{bot_id}/ownership', 200, 'owner', None, 'data.owner_user_id', 'OWNER_USER_ID',
     (141, 142), (138, 138), '    assert_json_eq "ownership identifies the registered owner" \\',
     '    _au_request GET "${prefix}/bots/${owner_bot}/ownership" "$principal_a"'),
    ('manager-page-empty-status', 'manager-page-empty', 'managers.page.empty', 'require_status', 'GET',
     '/api/v1/collaboration/bots/{bot_id}/managers', 200, 'owner', None, 'http_status', None, (146, 146), (145, 145),
     '    require_status "owner reads an empty manager page" "200" || return',
     '    _au_request GET "${prefix}/bots/${owner_bot}/managers" "$principal_a"'),
    ('manager-page-starts-empty', 'manager-page-empty', 'managers.page.empty', 'assert_json_eq', 'GET',
     '/api/v1/collaboration/bots/{bot_id}/managers', 200, 'owner', None, 'data.total', '0', (147, 147), (145, 145),
     '    assert_json_eq "manager page starts empty" "$RESPONSE" "data.total" "0"',
     '    _au_request GET "${prefix}/bots/${owner_bot}/managers" "$principal_a"'),
    ('owner-manager-add-rejected-status', 'owner-manager-add-rejected', 'managers.add.owner.rejected',
     'require_status', 'PUT', '/api/v1/collaboration/bots/{bot_id}/managers/{owner_user_id}', 409, 'owner', None,
     'http_status', None, (152, 152), (151, 151),
     '    require_status "adding the owner as a manager is rejected" "409" || return',
     '    _au_request PUT "${prefix}/bots/${owner_bot}/managers/${BCS_MOCK_USER_ID}" "$principal_a"'),
    ('owner-manager-add-error-code', 'owner-manager-add-rejected', 'managers.add.owner.rejected', 'assert_json_eq',
     'PUT', '/api/v1/collaboration/bots/{bot_id}/managers/{owner_user_id}', 409, 'owner', None, 'data.error_code',
     'owner_not_manager', (153, 154), (151, 151), '    assert_json_eq "owner add uses the stable error code" \\',
     '    _au_request PUT "${prefix}/bots/${owner_bot}/managers/${BCS_MOCK_USER_ID}" "$principal_a"'),
    ('manager-add-status', 'manager-add-target', 'managers.add.target', 'require_status', 'PUT',
     '/api/v1/collaboration/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None, 'http_status', None,
     (157, 157), (156, 156), '    require_status "owner adds the target human as a manager" "200" || return',
     '    _au_request PUT "${prefix}/bots/${owner_bot}/managers/${_AU_STAFF_B}" "$principal_a"'),
    ('manager-add-added-one', 'manager-add-target', 'managers.add.target', 'assert_json_eq', 'PUT',
     '/api/v1/collaboration/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None, 'data.added', '1',
     (158, 158), (156, 156), '    assert_json_eq "manager add reports one added" "$RESPONSE" "data.added" "1"',
     '    _au_request PUT "${prefix}/bots/${owner_bot}/managers/${_AU_STAFF_B}" "$principal_a"'),
    ('manager-page-populated-status', 'manager-page-populated', 'managers.page.populated', 'require_status', 'GET',
     '/api/v1/collaboration/bots/{bot_id}/managers', 200, 'owner', None, 'http_status', None, (161, 161), (160, 160),
     '    require_status "owner reads the populated manager page" "200" || return',
     '    _au_request GET "${prefix}/bots/${owner_bot}/managers" "$principal_a"'),
    ('manager-page-keeps-manager', 'manager-page-populated', 'managers.page.populated', 'assert_json_eq', 'GET',
     '/api/v1/collaboration/bots/{bot_id}/managers', 200, 'owner', None, 'data.total', '1', (162, 162), (160, 160),
     '    assert_json_eq "manager page keeps the manager" "$RESPONSE" "data.total" "1"',
     '    _au_request GET "${prefix}/bots/${owner_bot}/managers" "$principal_a"'),
    ('manager-page-exposes-added-manager', 'manager-page-populated', 'managers.page.populated',
     'assert_json_array_contains', 'GET', '/api/v1/collaboration/bots/{bot_id}/managers', 200, 'owner', None,
     'data.items', 'TARGET_USER_ID', (163, 164), (160, 160),
     '    assert_json_array_contains "manager page exposes the added manager" \\',
     '    _au_request GET "${prefix}/bots/${owner_bot}/managers" "$principal_a"'),
    ('manager-remove-status', 'manager-remove-target', 'managers.remove.target', 'require_status', 'DELETE',
     '/api/v1/collaboration/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None, 'http_status', None,
     (168, 168), (167, 167), '    require_status "owner removes the manager" "200" || return',
     '    _au_request DELETE "${prefix}/bots/${owner_bot}/managers/${_AU_STAFF_B}" "$principal_a"'),
    ('manager-remove-removed-one', 'manager-remove-target', 'managers.remove.target', 'assert_json_eq', 'DELETE',
     '/api/v1/collaboration/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None, 'data.removed', '1',
     (169, 169), (167, 167),
     '    assert_json_eq "manager removal reports one removed" "$RESPONSE" "data.removed" "1"',
     '    _au_request DELETE "${prefix}/bots/${owner_bot}/managers/${_AU_STAFF_B}" "$principal_a"'),
    ('manager-absent-remove-status', 'manager-absent-remove', 'managers.remove.absent', 'require_status', 'DELETE',
     '/api/v1/collaboration/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None, 'http_status', None,
     (171, 171), (170, 170), '    require_status "manager removal is idempotent for absent" "200" || return',
     '    _au_request DELETE "${prefix}/bots/${owner_bot}/managers/${_AU_STAFF_B}" "$principal_a"'),
    ('manager-absent-remove-removed-zero', 'manager-absent-remove', 'managers.remove.absent', 'assert_json_eq',
     'DELETE', '/api/v1/collaboration/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None, 'data.removed',
     '0', (172, 172), (170, 170),
     '    assert_json_eq "absent manager removal reports no change" "$RESPONSE" "data.removed" "0"',
     '    _au_request DELETE "${prefix}/bots/${owner_bot}/managers/${_AU_STAFF_B}" "$principal_a"'),
    ('manager-restore-status', 'manager-restore-target', 'managers.restore.target', 'require_status', 'PUT',
     '/api/v1/collaboration/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None, 'http_status', None,
     (176, 176), (175, 175), '    require_status "manager is restored before the transfer" "200" || return',
     '    _au_request PUT "${prefix}/bots/${owner_bot}/managers/${_AU_STAFF_B}" "$principal_a"'),
    ('mine-ensured-human-mine-status', 'mine-ensured-read', 'public.mine.read', 'require_status', 'GET',
     '/openapi/v1/collaboration/bots/mine', 200, 'mine', ('limit', '20'), 'http_status', None, (199, 199), (197, 198),
     '    require_status "mine-ensured human reads its own mine" "200" || return',
     '    api_request_headers GET "${public_prefix}/bots/mine?limit=20" "" \\'),
    ('mine-manager-add-status', 'mine-manager-add', 'managers.add.mine', 'require_status', 'PUT',
     '/api/v1/collaboration/bots/{bot_id}/managers/{mine_user_id}', 200, 'owner', None, 'http_status', None,
     (202, 202), (201, 201), '    require_status "mine-ensured human is a valid manager add target" "200" || return',
     '    _au_request PUT "${prefix}/bots/${owner_bot}/managers/${mine_only_staff}" "$principal_a"'),
    ('mine-manager-add-added-one', 'mine-manager-add', 'managers.add.mine', 'assert_json_eq', 'PUT',
     '/api/v1/collaboration/bots/{bot_id}/managers/{mine_user_id}', 200, 'owner', None, 'data.added', '1', (203, 204),
     (201, 201), '    assert_json_eq "mine-ensured manager add reports one added" \\',
     '    _au_request PUT "${prefix}/bots/${owner_bot}/managers/${mine_only_staff}" "$principal_a"'),
    ('never-seen-manager-add-status', 'never-seen-add', 'managers.add.never', 'require_status', 'PUT',
     '/api/v1/collaboration/bots/{bot_id}/managers/{never_user_id}', 400, 'owner', None, 'http_status', None,
     (209, 209), (208, 208), '    require_status "never-seen human is not a manager add target" "400" || return',
     '    _au_request PUT "${prefix}/bots/${owner_bot}/managers/never-exists-$$" "$principal_a"'),
    ('never-seen-manager-error-code', 'never-seen-add', 'managers.add.never', 'assert_json_eq', 'PUT',
     '/api/v1/collaboration/bots/{bot_id}/managers/{never_user_id}', 400, 'owner', None, 'data.error_code',
     'bot_authority_human_unknown', (210, 211), (208, 208),
     '    assert_json_eq "unknown manager add uses the stable error code" \\',
     '    _au_request PUT "${prefix}/bots/${owner_bot}/managers/never-exists-$$" "$principal_a"'),
    ('mine-manager-remove-status', 'mine-manager-remove', 'managers.remove.mine', 'require_status', 'DELETE',
     '/api/v1/collaboration/bots/{bot_id}/managers/{mine_user_id}', 200, 'owner', None, 'http_status', None,
     (216, 216), (215, 215),
     '    require_status "mine-ensured manager is removed before the transfer" "200" || return',
     '    _au_request DELETE "${prefix}/bots/${owner_bot}/managers/${mine_only_staff}" "$principal_a"'),
    ('mine-manager-remove-removed-one', 'mine-manager-remove', 'managers.remove.mine', 'assert_json_eq', 'DELETE',
     '/api/v1/collaboration/bots/{bot_id}/managers/{mine_user_id}', 200, 'owner', None, 'data.removed', '1',
     (217, 218), (215, 215), '    assert_json_eq "mine-ensured manager removal reports one removed" \\',
     '    _au_request DELETE "${prefix}/bots/${owner_bot}/managers/${mine_only_staff}" "$principal_a"'),
    ('transfer-initiate-status', 'transfer-initiate', 'transfer.initiate', 'require_status', 'PUT',
     '/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 201, 'owner', None,
     'http_status', None, (223, 223), (222, 222),
     '    require_status "owner initiates the ownership transfer" "201" || return',
     '    _au_request PUT "$transfer_path" "$principal_a"'),
    ('transfer-initiate-pending', 'transfer-initiate', 'transfer.initiate', 'assert_json_eq', 'PUT',
     '/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 201, 'owner', None,
     'data.status', 'pending', (226, 226), (222, 222),
     '    assert_json_eq "initiated transfer is pending" "$RESPONSE" "data.status" "pending"',
     '    _au_request PUT "$transfer_path" "$principal_a"'),
    ('transfer-initiate-target-owner', 'transfer-initiate', 'transfer.initiate', 'assert_json_eq', 'PUT',
     '/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 201, 'owner', None,
     'data.target_owner_user_id', 'TARGET_USER_ID', (227, 228), (222, 222),
     '    assert_json_eq "initiated transfer names the target" \\',
     '    _au_request PUT "$transfer_path" "$principal_a"'),
    ('transfer-refresh-status', 'transfer-refresh', 'transfer.refresh', 'require_status', 'PUT',
     '/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 200, 'owner', None,
     'http_status', None, (231, 231), (230, 230),
     '    require_status "transfer refresh reuses the pending round" "200" || return',
     '    _au_request PUT "$transfer_path" "$principal_a"'),
    ('transfer-refresh-window-renewed', 'transfer-refresh', 'transfer.refresh', 'assert_ok', 'PUT',
     '/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 200, 'owner', None,
     'data.expires_at', 'RENEWAL_WINDOW_MS', (240, 245), (230, 230),
     '    assert_ok "transfer refresh renews the window without shrinking it" \\',
     '    _au_request PUT "$transfer_path" "$principal_a"'),
    ('transfer-target-read-status', 'transfer-target-read', 'transfer.target.read', 'require_status', 'GET',
     '/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 200, 'target', None,
     'http_status', None, (248, 248), (247, 247),
     '    require_status "target human reads the pending transfer" "200" || return',
     '    _au_request GET "$transfer_path" "$principal_b"'),
    ('transfer-target-read-pending', 'transfer-target-read', 'transfer.target.read', 'assert_json_eq', 'GET',
     '/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 200, 'target', None,
     'data.status', 'pending', (249, 249), (247, 247),
     '    assert_json_eq "target reads a pending transfer" "$RESPONSE" "data.status" "pending"',
     '    _au_request GET "$transfer_path" "$principal_b"'),
    ('transfer-accept-status', 'transfer-accept', 'transfer.accept', 'require_status', 'POST',
     '/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/accept', 200, 'target', None,
     'http_status', None, (252, 252), (251, 251),
     '    require_status "target human accepts the ownership transfer" "200" || return',
     '    _au_request POST "${transfer_path}/accept" "$principal_b"'),
    ('transfer-accept-accepted', 'transfer-accept', 'transfer.accept', 'assert_json_eq', 'POST',
     '/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/accept', 200, 'target', None,
     'data.status', 'accepted', (253, 253), (251, 251),
     '    assert_json_eq "accept records the accepted status" "$RESPONSE" "data.status" "accepted"',
     '    _au_request POST "${transfer_path}/accept" "$principal_b"'),
    ('transferred-ownership-status', 'transferred-ownership-read', 'ownership.target.read', 'require_status', 'GET',
     '/api/v1/collaboration/bots/{bot_id}/ownership', 200, 'target', None, 'http_status', None, (256, 256),
     (255, 255), '    require_status "the new owner reads the collaboration ownership view" "200" || return',
     '    _au_request GET "${prefix}/bots/${owner_bot}/ownership" "$principal_b"'),
    ('transferred-ownership-owner-moved', 'transferred-ownership-read', 'ownership.target.read', 'assert_json_eq',
     'GET', '/api/v1/collaboration/bots/{bot_id}/ownership', 200, 'target', None, 'data.owner_user_id',
     'TARGET_USER_ID', (257, 258), (255, 255), '    assert_json_eq "ownership transfer moved the owner" \\',
     '    _au_request GET "${prefix}/bots/${owner_bot}/ownership" "$principal_b"'),
    ('transferred-ledger-status', 'transferred-ledger-read', 'managers.ledger.target.read', 'require_status', 'GET',
     '/api/v1/collaboration/bots/{bot_id}/managers', 200, 'target', None, 'http_status', None, (261, 261), (260, 260),
     '    require_status "the new owner reads the transferred manager ledger" "200" || return',
     '    _au_request GET "${prefix}/bots/${owner_bot}/managers" "$principal_b"'),
    ('transferred-ledger-owner-names-target', 'transferred-ledger-read', 'managers.ledger.target.read',
     'assert_json_eq', 'GET', '/api/v1/collaboration/bots/{bot_id}/managers', 200, 'target', None,
     'data.owner.owner_user_id', 'TARGET_USER_ID', (262, 263), (260, 260),
     '    assert_json_eq "the transferred owner is in the ledger" \\',
     '    _au_request GET "${prefix}/bots/${owner_bot}/managers" "$principal_b"'),
    ('transferred-ledger-original-owner-plain-manager', 'transferred-ledger-read', 'managers.ledger.target.read',
     'assert_json_array_contains', 'GET', '/api/v1/collaboration/bots/{bot_id}/managers', 200, 'target', None,
     'data.items', 'OWNER_USER_ID', (264, 265), (260, 260),
     '    assert_json_array_contains "the original owner is now a plain manager" \\',
     '    _au_request GET "${prefix}/bots/${owner_bot}/managers" "$principal_b"'),
    ('received-inbox-status', 'received-inbox', 'transfers.inbox.received', 'require_status', 'GET',
     '/api/v1/collaboration/ownership-transfers', 200, 'target', ('direction', 'received'), 'http_status', None,
     (269, 269), (268, 268), '    require_status "the new owner lists received transfers" "200" || return',
     '    _au_request GET "${prefix}/ownership-transfers?direction=received" "$principal_b"'),
    ('received-inbox-contains-bot', 'received-inbox', 'transfers.inbox.received', 'assert_contains', 'GET',
     '/api/v1/collaboration/ownership-transfers', 200, 'target', ('direction', 'received'), 'raw_body',
     'OWNER_BOT_ID', (270, 270), (268, 268),
     '    assert_contains "received inbox contains the accepted bot" "$RESPONSE" "$owner_bot"',
     '    _au_request GET "${prefix}/ownership-transfers?direction=received" "$principal_b"'),
    ('sent-inbox-status', 'sent-inbox', 'transfers.inbox.sent', 'require_status', 'GET',
     '/api/v1/collaboration/ownership-transfers', 200, 'owner', ('direction', 'sent'), 'http_status', None,
     (273, 273), (272, 272), '    require_status "the ex-owner lists sent transfers" "200" || return',
     '    _au_request GET "${prefix}/ownership-transfers?direction=sent" "$principal_a"'),
    ('sent-inbox-contains-bot', 'sent-inbox', 'transfers.inbox.sent', 'assert_contains', 'GET',
     '/api/v1/collaboration/ownership-transfers', 200, 'owner', ('direction', 'sent'), 'raw_body', 'OWNER_BOT_ID',
     (274, 274), (272, 272), '    assert_contains "sent outbox contains the accepted bot" "$RESPONSE" "$owner_bot"',
     '    _au_request GET "${prefix}/ownership-transfers?direction=sent" "$principal_a"'),
)

# Disclosed projection exclusions: every story-function line not claimed by an
# obligation assertion span or its upstream call span. The ledger tiles
# 93-275 exactly with the 62 spans above; blank-only blocks carry an empty
# verbatim quote and are validated as all-blank.
_EXCLUSION_ROWS = (
    (93, 95, 93, 'story_authority_user_journey() {',
     'story header, narration and the initial blank line'),
    (96, 99, 96, '    local prefix="/api/v1/collaboration"',
     'local declarations: prefix, signing-key environment name, probe path, principal variables'),
    (100, 109, 100, '    if ! principal_a=$(python3 "$probe" principal \\',
     'control-flow: owner Gateway Principal minting probe plus its fail branch (bootstrap)'),
    (110, 120, 110, '    if ! principal_b=$(python3 "$probe" principal \\',
     'control-flow: target Gateway Principal minting probe, its fail branch, trailing blank (bootstrap)'),
    (121, 132, 121, "    # 1. Trusted registration: A's owned Bot and B's own Bot (which",
     'control-flow guard: trusted registration comment, locals, owner Bot registration call, fail guard'),
    (133, 136, 133, '    _au_register_fixture_bot "$_AU_STAFF_B" "authority-story-b-$$-$(date +%s)" >/dev/null \\',
     'control-flow guard: target human fixture registration call, its fail guard and the trailing blank'),
    (137, 137, 137, '    # 2. Ownership read shows the registration-committed owner.',
     'chapter comment: ownership read'),
    (143, 144, 144, '    # 3. Manager single-item surface, starting from an empty page.',
     'blank line and chapter comment: manager single-item surface'),
    (148, 150, 149, '    # Polarity on a stable public code: the owner is never a manager',
     'blank line and polarity comment: owner is never a manager operand'),
    (155, 155, None, '',
     'blank separator line'),
    (159, 159, None, '',
     'blank separator line'),
    (165, 166, 166, '    # Delete, then the idempotent re-delete (absents stay successful).',
     'blank line and comment: delete then idempotent re-delete'),
    (173, 174, 174, '    # Re-add so the transfer has a live manager ledger to update.',
     'blank line and comment: re-add before transfer'),
    (177, 183, 178, '    # 2b. A Human ensured ONLY through a collaboration entry (the',
     'blank line and section 2b comment: mine-ensured human through the public read only'),
    (184, 186, 184, '    local mine_only_staff principal_mine public_prefix',
     'mine-only locals: mine_only_staff, principal_mine, public_prefix'),
    (187, 196, 187, '    if ! principal_mine=$(python3 "$probe" principal \\',
     'control-flow: mine-ensured human Gateway Principal minting plus its fail branch (non-assertion bootstrap)'),
    (200, 200, None, '',
     'blank separator line'),
    (205, 207, 206, '    # Strict-edit polarity: a never-seen user stays unknown (the',
     'blank line and strict-edit polarity comment: never-seen user stays unknown'),
    (212, 214, 213, '    # Restore the story set (the transfer chapter below owns the',
     'blank line and comment: restore the story set before the transfer chapter'),
    (219, 221, 220, '    # 3. Ownership transfer chain: initiate → refresh → accept.',
     'blank line, chapter 3 comment, and the transfer_path local construction'),
    (224, 225, 224, '    local expires_at',
     'expires_at local declaration and extraction from the initiated response'),
    (229, 229, None, '',
     'blank separator line'),
    (232, 239, 232, '    # spec §6.2: a same-target refresh RENEWS requested_at and the',
     'renewal-window spec comment plus refreshed_expires locals and extraction'),
    (246, 246, None, '',
     'blank separator line'),
    (250, 250, None, '',
     'blank separator line'),
    (254, 254, None, '',
     'blank separator line'),
    (259, 259, None, '',
     'blank separator line'),
    (266, 267, 267, '    # 4. Both transfer inboxes observe the accepted round.',
     'blank line and chapter 4 comment: both transfer inboxes'),
    (271, 271, None, '',
     'blank separator line'),
    (275, 275, 275, '}',
     'story function closing brace'),
)

PROJECTION_EXCLUSIONS = tuple(
    {"line_start": start, "line_end": end, "quote_line": quote_line,
     "quote": quote, "reason": reason}
    for start, end, quote_line, quote, reason in _EXCLUSION_ROWS
)


def _source_lines() -> list[str]:
    raw = SOURCE_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise RunStateError("authority upstream source sha256 drifted")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise RunStateError(f"authority upstream source is not utf-8: {error}") from None
    return lines


def parsed_obligations() -> tuple[Obligation, ...]:
    rows = tuple(Obligation(
        obligation_id=OBLIGATION_PREFIX + slug,
        parent_obligation=GROUP_PREFIX + group,
        family="authority",
        level="shell",
        method=method,
        assertion=kind,
        line_start=aspan[0],
        line_end=aspan[1],
        quote=quote,
        call_line_start=cspan[0],
        call_line_end=cspan[1],
        call_quote=call_quote,
        path_template=path,
        status=status,
        actor=actor,
        query=query,
        operation=operation,
        field=field,
        expected=expected,
        density=True,
    ) for slug, group, operation, kind, method, path, status, actor, query,
               field, expected, aspan, cspan, quote, call_quote in _ROWS)
    if len(rows) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("authority density roster count changed")
    if len({row.obligation_id for row in rows}) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("authority density roster ids are not unique")
    if sum(row.assertion == "require_status" for row in rows) != SHELL_STATUS_ASSERTIONS:
        raise RunStateError("authority density status-assertion count changed")
    if sum(row.assertion != "require_status" for row in rows) != SHELL_VALUE_ASSERTIONS:
        raise RunStateError("authority density value-assertion count changed")
    return rows


def call_groups() -> tuple[dict[str, Any], ...]:
    rows = parsed_obligations()
    order: list[str] = []
    members: dict[str, list[Obligation]] = {}
    for row in rows:
        if row.parent_obligation not in members:
            order.append(row.parent_obligation)
            members[row.parent_obligation] = []
        members[row.parent_obligation].append(row)
    groups = []
    for group_id in order:
        rows_in_group = members[group_id]
        first = rows_in_group[0]
        signature = {(row.method, row.path_template, row.status, row.actor,
                      row.query, row.call_line_start, row.call_line_end)
                     for row in rows_in_group}
        if len(signature) != 1:
            raise RunStateError(f"authority call group is not one upstream call: {group_id}")
        groups.append({
            "group_id": group_id,
            "label": first.parent_obligation[len(GROUP_PREFIX):],
            "obligation_ids": [row.obligation_id for row in rows_in_group],
            "method": first.method,
            "path_template": first.path_template,
            "status": first.status,
            "actor": first.actor,
            "query": (dict([first.query]) if first.query else None),
            "line_start": first.call_line_start,
            "line_end": first.call_line_end,
            "quote": first.call_quote,
            "operation": first.operation,
        })
    if len(groups) != CALL_GROUP_COUNT:
        raise RunStateError("authority density call group count changed")
    return tuple(groups)


def _validate_roster_anchors(rows: tuple[Obligation, ...], lines: list[str]) -> None:
    total = len(lines)
    previous_call_start: int | None = None
    previous_call_end = 0
    for row in rows:
        if not (STORY_LINE_START <= row.call_line_start <= row.call_line_end
                <= row.line_start <= row.line_end <= STORY_LINE_END <= total):
            raise RunStateError(f"authority density span outside the story function: {row.obligation_id}")
        if row.quote != lines[row.line_start - 1]:
            raise RunStateError(f"authority density assertion quote drifted: {row.obligation_id}")
        if row.call_quote != lines[row.call_line_start - 1]:
            raise RunStateError(f"authority density call quote drifted: {row.obligation_id}")
        if row.method not in ("GET", "PUT", "DELETE", "POST"):
            raise RunStateError(f"authority density method is invalid: {row.obligation_id}")
        if row.actor not in ("owner", "target", "mine"):
            raise RunStateError(f"authority density actor is invalid: {row.obligation_id}")
        if row.assertion not in ASSERTION_KINDS:
            raise RunStateError(f"authority density assertion kind is invalid: {row.obligation_id}")
        if not (row.call_line_end <= row.line_start):
            raise RunStateError(f"authority density call must precede its assertion: {row.obligation_id}")
        if previous_call_start is None or row.call_line_start != previous_call_start:
            if row.call_line_start <= previous_call_end:
                raise RunStateError(f"authority density call spans are out of order: {row.obligation_id}")
            previous_call_start = row.call_line_start
            previous_call_end = row.call_line_end
    if tuple(row.obligation_id for row in rows) != tuple(
            OBLIGATION_PREFIX + slug for slug, *_ in _ROWS):
        raise RunStateError("authority density roster order changed")


def _validate_tiling(rows: tuple[Obligation, ...], lines: list[str]) -> None:
    covered: dict[int, str] = {}

    def mark(start: int, end: int, label: str) -> None:
        for line in range(start, end + 1):
            if line in covered:
                raise RunStateError(
                    f"authority density tiling overlap at line {line} ({label} vs {covered[line]})")
            covered[line] = label

    previous_call_span: tuple[int, int] | None = None
    for row in rows:
        call_span = (row.call_line_start, row.call_line_end)
        if call_span != previous_call_span:
            mark(call_span[0], call_span[1], row.obligation_id + ".call")
            previous_call_span = call_span
        mark(row.line_start, row.line_end, row.obligation_id + ".assertion")
    for exclusion in PROJECTION_EXCLUSIONS:
        start, end = exclusion["line_start"], exclusion["line_end"]
        quote, quote_line = exclusion["quote"], exclusion["quote_line"]
        if quote == "":
            if any(lines[index - 1].strip() for index in range(start, end + 1)):
                raise RunStateError("authority density blank exclusion carries text")
        else:
            if quote_line is None or not (start <= quote_line <= end):
                raise RunStateError("authority density exclusion quote line is outside its span")
            if lines[quote_line - 1] != quote:
                raise RunStateError("authority density exclusion quote drifted")
        mark(start, end, "projection_exclusion")
    body = set(range(STORY_LINE_START, STORY_LINE_END + 1))
    missing = body - set(covered)
    if missing:
        raise RunStateError("authority density tiling leaves story lines uncharged: "
                            + ",".join(str(line) for line in sorted(missing)[:8]) + "...")


def authority_density_plan() -> dict[str, Any]:
    """Freeze, anchor and tile the 42-obligation density roster."""
    lines = _source_lines()
    text = "\n".join(lines)
    if not text.startswith("#!/bin/bash"):
        raise RunStateError("authority upstream source lost its shebang")
    if len([match for match in (STORY_FUNCTION + "() {",) if match in text.splitlines()]) != 1:
        raise RunStateError("authority parent story function disappeared")
    rows = parsed_obligations()
    _validate_roster_anchors(rows, lines)
    groups = call_groups()
    _validate_tiling(rows, lines)
    status_rows = [row for row in rows if row.assertion == "require_status"]
    value_rows = [row for row in rows if row.assertion != "require_status"]
    if len(status_rows) != SHELL_STATUS_ASSERTIONS or len(value_rows) != SHELL_VALUE_ASSERTIONS:
        raise RunStateError("authority density kind accounting changed")
    if len({group["operation"] for group in groups}) != CALL_GROUP_COUNT:
        raise RunStateError("authority density call group operations are not unique")
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": CONTRACT,
        "parent": PARENT,
        "case_id": CASE_ID,
        "source": {
            "file": SOURCE_FILE,
            "sha256": SOURCE_SHA256,
            "source_revision": SOURCE_REVISION,
            "story_function": STORY_FUNCTION,
            "line_start": STORY_LINE_START,
            "line_end": STORY_LINE_END,
        },
        "accounting": {
            "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
            "call_group_count": CALL_GROUP_COUNT,
            "shell_status_assertions": SHELL_STATUS_ASSERTIONS,
            "shell_value_assertions": SHELL_VALUE_ASSERTIONS,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
            "parsed_coverage": f"{PARSED_OBLIGATION_COUNT}/{PARSED_OBLIGATION_COUNT}",
        },
        "obligations": [row.as_dict() for row in rows],
        "call_groups": list(groups),
        "projection_exclusions": list(PROJECTION_EXCLUSIONS),
        "unlocated_source_probes": [],
        "unlocated_count": 0,
        "replay_scope": {
            "business_operations": BUSINESS_HTTP_CALLS,
            "bootstrap_operations_excluded": BOOTSTRAP_HTTP_CALLS,
            "bootstrap_exclusion_reason": (
                "trusted registration mint/commit calls provision fixtures and carry no "
                "shell assertion; the 42-obligation roster rides the 20 business calls only"),
            "family_http_matrix": HTTP_CALL_COUNT,
        },
        "environment_posture": dict(ENVIRONMENT_POSTURE),
        "source_read_only": True,
    }
