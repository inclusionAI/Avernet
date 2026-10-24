#!/bin/bash
# authority.sh — Bot-authority collaboration API user story.
#
# Drives the `/api/v1/collaboration` authority surface end to end
# through REAL flows, mirroring the Rust harness chain test
# (`crates/bootstrap/bcs/tests/e2e_bot_authority.rs`) in the story
# suite:
#
#   1. trusted registration seeds the owner human (A = the mock
#      story user) and the target human (B) — B's `human_` actor
#      row comes from that registration, while the extra
#      mine-ensured human (C) is created ONLY by the public
#      `bots/mine` read: the authority's Human registry is the
#      live `bcs_bots` actor row (spec §3.1), never the OAuth
#      identity table;
#   2. the owner (A) reads ownership, exercises the manager
#      single-item surface (add / list / idempotent remove),
#      hits the `owner_not_manager` polarity, adds the
#      mine-ensured human C (a strict-edit acceptance the old
#      identity-table guard wrongly rejected), and hits the
#      `bot_authority_human_unknown` polarity for a never-seen
#      user;
#   3. the transfer chain: initiate (201) → refresh (same window)
#      → the target reads it → accept → A is the plain manager and
#      B is the owner;
#   4. both inboxes (`direction=received` / `sent`) list the
#      accepted round.
#
# Lenient-sync note (spec §5.2): the trusted-App full-set sync
# legs (unknown/malformed member skipped, missing target bot
# zeros) are covered by the shared conformance suites and the
# Rust e2e chain (`e2e_bot_authority.rs` configures a
# trusted-app rule); THIS script's stack has no trusted-app
# scope configured, so the sync surface cannot be driven here
# without a mid-story server restart. The bash story pins the
# human-registry fix through the mine-ensured edit leg instead.
#
# Auth: every `/api/v1/collaboration` call carries the Gateway
# Principal JWT (`X-Avernet-Principal`, signed with
# AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE) via the same probe
# the public collaboration story uses (`group_session_ws_probe.py`).

# Test registration (consumed by e2e.sh)
E2E_TESTS_STORIES+=("story_authority_user_journey")

# The secondary fixture human (the manager/transfer target). Its
# `human_<id>` actor row is created by its own trusted registration
# below, mirroring the Backend-side human pre-seed in production.
_AU_STAFF_B="${BCS_E2E_AUTHORITY_STAFF_B:-002}"

# Register a bot through the TRUSTED register surface under the
# given mock user id. Echoes "<bot_uuid> <bot_token>". This both
# creates a known owned Bot and ensures the `human_<staff>` actor
# row exists so the Bot can name the human as a manager or
# transfer target.
_au_register_fixture_bot() {
    local staff="$1" name="$2"
    local mint token enc_token enc_name register
    mint="$(curl --noproxy '*' -s -S \
        -H "X-Mock-User-Id: $staff" \
        -H "X-Mock-Nick-Name: $staff" \
        "${BCS_API_BASE_URL}/register/token")" || return 1
    token="$(printf '%s' "$mint" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("token", ""))
except Exception:
    print("")
' 2>/dev/null)"
    [[ -n "$token" ]] || return 1
    enc_token="$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$token")"
    enc_name="$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$name")"
    register="$(curl --noproxy '*' -s -S -X POST \
        "${BCS_API_BASE_URL}/register?token=${enc_token}&bot-name=${enc_name}")" || return 1
    printf '%s' "$register" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get("bot_uuid", ""), d.get("bot_token", ""))
except Exception:
    print("", "")
' 2>/dev/null
}

# Request the authority API as the given Gateway Principal.
# Sets HTTP_STATUS / RESPONSE like the api_* helpers.
_au_request() {
    local method="$1" path="$2" principal="$3" body="${4:-}"
    api_request_headers "$method" "$path" "$body" \
        "X-Avernet-Principal: ${principal}"
}

story_authority_user_journey() {
    info "Story: an owner human manages managers and transfers bot ownership"

    local prefix="/api/v1/collaboration"
    local signing_key="${AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE:-avernet-dev-signing-key-NOT-FOR-PROD}"
    local probe="${SCRIPT_DIR}/group_session_ws_probe.py"
    local principal_a principal_b
    if ! principal_a=$(python3 "$probe" principal \
        --user-id "$BCS_MOCK_USER_ID" \
        --username "$BCS_MOCK_USER_NICK_NAME" \
        --tenant "bcs-e2e" \
        --signing-key "$signing_key"); then
        fail "Gateway Principal generation failed for the owner human"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi
    if ! principal_b=$(python3 "$probe" principal \
        --user-id "$_AU_STAFF_B" \
        --username "$_AU_STAFF_B" \
        --tenant "bcs-e2e" \
        --signing-key "$signing_key"); then
        fail "Gateway Principal generation failed for the target human"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi

    # 1. Trusted registration: A's owned Bot and B's own Bot (which
    #    seeds the human_B actor row needed for manager/transfer
    #    targets). Fresh ids per run keep re-runs idempotent.
    local fixtures owner_bot
    fixtures=$(_au_register_fixture_bot "$BCS_MOCK_USER_ID" "authority-story-$$-$(date +%s)") || true
    owner_bot="$(printf '%s' "$fixtures" | awk '{print $1}')"
    if [ -z "$owner_bot" ]; then
        fail "trusted registration of the owner fixture bot failed"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi
    _au_register_fixture_bot "$_AU_STAFF_B" "authority-story-b-$$-$(date +%s)" >/dev/null \
        || { fail "trusted registration of the target human fixture failed"
             TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return; }

    # 2. Ownership read shows the registration-committed owner.
    _au_request GET "${prefix}/bots/${owner_bot}/ownership" "$principal_a"
    require_status "owner reads the collaboration ownership view" "200" || return
    assert_json_eq "ownership keeps the bot id" "$RESPONSE" "data.bot_id" "$owner_bot"
    assert_json_eq "ownership identifies the registered owner" \
        "$RESPONSE" "data.owner_user_id" "$BCS_MOCK_USER_ID"

    # 3. Manager single-item surface, starting from an empty page.
    _au_request GET "${prefix}/bots/${owner_bot}/managers" "$principal_a"
    require_status "owner reads an empty manager page" "200" || return
    assert_json_eq "manager page starts empty" "$RESPONSE" "data.total" "0"

    # Polarity on a stable public code: the owner is never a manager
    # operand (spec §5.1).
    _au_request PUT "${prefix}/bots/${owner_bot}/managers/${BCS_MOCK_USER_ID}" "$principal_a"
    require_status "adding the owner as a manager is rejected" "409" || return
    assert_json_eq "owner add uses the stable error code" \
        "$RESPONSE" "data.error_code" "owner_not_manager"

    _au_request PUT "${prefix}/bots/${owner_bot}/managers/${_AU_STAFF_B}" "$principal_a"
    require_status "owner adds the target human as a manager" "200" || return
    assert_json_eq "manager add reports one added" "$RESPONSE" "data.added" "1"

    _au_request GET "${prefix}/bots/${owner_bot}/managers" "$principal_a"
    require_status "owner reads the populated manager page" "200" || return
    assert_json_eq "manager page keeps the manager" "$RESPONSE" "data.total" "1"
    assert_json_array_contains "manager page exposes the added manager" \
        "$RESPONSE" "data.items" "$_AU_STAFF_B"

    # Delete, then the idempotent re-delete (absents stay successful).
    _au_request DELETE "${prefix}/bots/${owner_bot}/managers/${_AU_STAFF_B}" "$principal_a"
    require_status "owner removes the manager" "200" || return
    assert_json_eq "manager removal reports one removed" "$RESPONSE" "data.removed" "1"
    _au_request DELETE "${prefix}/bots/${owner_bot}/managers/${_AU_STAFF_B}" "$principal_a"
    require_status "manager removal is idempotent for absent" "200" || return
    assert_json_eq "absent manager removal reports no change" "$RESPONSE" "data.removed" "0"

    # Re-add so the transfer has a live manager ledger to update.
    _au_request PUT "${prefix}/bots/${owner_bot}/managers/${_AU_STAFF_B}" "$principal_a"
    require_status "manager is restored before the transfer" "200" || return

    # 2b. A Human ensured ONLY through a collaboration entry (the
    #     public `bots/mine` read; NO trusted registration) is a
    #     valid strict-edit target: the authority Human registry is
    #     the `bcs_bots` actor row that read creates (spec §3.1).
    #     Before the registry fix this add was wrongly rejected as
    #     `bot_authority_human_unknown`.
    local mine_only_staff principal_mine public_prefix
    public_prefix="/openapi/v1/collaboration"
    mine_only_staff="${BCS_E2E_AUTHORITY_STAFF_MINE:-137}"
    if ! principal_mine=$(python3 "$probe" principal \
            --user-id "$mine_only_staff" \
            --username "$mine_only_staff" \
            --tenant "bcs-e2e" \
            --signing-key "$signing_key"); then
        fail "Gateway Principal generation failed for the mine-ensured human"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi
    api_request_headers GET "${public_prefix}/bots/mine?limit=20" "" \
        "X-Avernet-Principal: ${principal_mine}"
    require_status "mine-ensured human reads its own mine" "200" || return

    _au_request PUT "${prefix}/bots/${owner_bot}/managers/${mine_only_staff}" "$principal_a"
    require_status "mine-ensured human is a valid manager add target" "200" || return
    assert_json_eq "mine-ensured manager add reports one added" \
        "$RESPONSE" "data.added" "1"

    # Strict-edit polarity: a never-seen user stays unknown (the
    # edit does NOT read leniency from the sync).
    _au_request PUT "${prefix}/bots/${owner_bot}/managers/never-exists-$$" "$principal_a"
    require_status "never-seen human is not a manager add target" "400" || return
    assert_json_eq "unknown manager add uses the stable error code" \
        "$RESPONSE" "data.error_code" "bot_authority_human_unknown"

    # Restore the story set (the transfer chapter below owns the
    # ledger from here).
    _au_request DELETE "${prefix}/bots/${owner_bot}/managers/${mine_only_staff}" "$principal_a"
    require_status "mine-ensured manager is removed before the transfer" "200" || return
    assert_json_eq "mine-ensured manager removal reports one removed" \
        "$RESPONSE" "data.removed" "1"

    # 3. Ownership transfer chain: initiate → refresh → accept.
    local transfer_path="${prefix}/bots/${owner_bot}/ownership-transfers/human_${_AU_STAFF_B}"
    _au_request PUT "$transfer_path" "$principal_a"
    require_status "owner initiates the ownership transfer" "201" || return
    local expires_at
    expires_at=$(printf '%s' "$RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["expires_at"])' 2>/dev/null)
    assert_json_eq "initiated transfer is pending" "$RESPONSE" "data.status" "pending"
    assert_json_eq "initiated transfer names the target" \
        "$RESPONSE" "data.target_owner_user_id" "$_AU_STAFF_B"

    _au_request PUT "$transfer_path" "$principal_a"
    require_status "transfer refresh reuses the pending round" "200" || return
    # spec §6.2: a same-target refresh RENEWS requested_at and the
    # seven-day window (renewable transfers); idempotency only promises
    # no second row and no transfer execution — the time fields need not
    # repeat. Assert the renewed expiry never shrinks and only moves
    # within the re-stamp gap; exact equality flakes at wall-second
    # boundaries (the conformance harness pins the same semantics).
    local refreshed_expires
    refreshed_expires=$(printf '%s' "$RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["expires_at"])' 2>/dev/null)
    assert_ok "transfer refresh renews the window without shrinking it" \
        python3 -c 'import sys
new, old = int(sys.argv[1]), int(sys.argv[2])
assert new >= old, "refreshed expiry must never shrink the window"
assert new - old < 300_000, "renewal stays within the re-stamp gap"' \
        "$refreshed_expires" "$expires_at"

    _au_request GET "$transfer_path" "$principal_b"
    require_status "target human reads the pending transfer" "200" || return
    assert_json_eq "target reads a pending transfer" "$RESPONSE" "data.status" "pending"

    _au_request POST "${transfer_path}/accept" "$principal_b"
    require_status "target human accepts the ownership transfer" "200" || return
    assert_json_eq "accept records the accepted status" "$RESPONSE" "data.status" "accepted"

    _au_request GET "${prefix}/bots/${owner_bot}/ownership" "$principal_b"
    require_status "the new owner reads the collaboration ownership view" "200" || return
    assert_json_eq "ownership transfer moved the owner" \
        "$RESPONSE" "data.owner_user_id" "$_AU_STAFF_B"

    _au_request GET "${prefix}/bots/${owner_bot}/managers" "$principal_b"
    require_status "the new owner reads the transferred manager ledger" "200" || return
    assert_json_eq "the transferred owner is in the ledger" \
        "$RESPONSE" "data.owner.owner_user_id" "$_AU_STAFF_B"
    assert_json_array_contains "the original owner is now a plain manager" \
        "$RESPONSE" "data.items" "$BCS_MOCK_USER_ID"

    # 4. Both transfer inboxes observe the accepted round.
    _au_request GET "${prefix}/ownership-transfers?direction=received" "$principal_b"
    require_status "the new owner lists received transfers" "200" || return
    assert_contains "received inbox contains the accepted bot" "$RESPONSE" "$owner_bot"

    _au_request GET "${prefix}/ownership-transfers?direction=sent" "$principal_a"
    require_status "the ex-owner lists sent transfers" "200" || return
    assert_contains "sent outbox contains the accepted bot" "$RESPONSE" "$owner_bot"
}
