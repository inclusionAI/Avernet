#!/bin/bash
# Sourced by edge_permission.sh; uses the shared E2E counters and HTTP mock.

# Human→TC-bot approval: the forwarded gateway principal is re-addressed as a
# bcs-issued token (iss=bcs) and the TC friend grant reaches the backend's
# /api/internal/bot-friend-auth/sync. This is the runtime contract between
# BCS and the backend — the backend refuses its own iss=backend echo, so the
# re-addressed copy is the only shape a work-order callback approval can sync
# in. Requires the Provider/Judge mock (which serves both the sync endpoint
# and the friend work-order event call), so it is skipped without it.
test_ep_tc_friend_auth_sync_readdressed_principal() {
    info "EdgePermission: TC friend-auth-sync re-addresses the forwarded principal"
    local mock_url="$(mock_view_base_url)"
    if [[ -z "$mock_url" ]]; then
        skip_case "no Provider/Judge mock; friend-auth-sync e2e needs it" || return 77
    fi

    local tc_bot="2026e2e-tc-bot:85020"
    local applicant="153998" owner="85020"
    local probe="${SCRIPT_DIR}/group_session_ws_probe.py"
    local signing_key="${AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE:-avernet-dev-signing-key-NOT-FOR-PROD}"
    # The approver arrives the way the friend work-order callback delivers it:
    # a principal the backend re-addressed to BCS (iss=backend).
    local principal applicant_principal
    if ! principal="$(python3 "$probe" principal \
        --user-id "$owner" --username "tc-sync-owner" --tenant "bcs-e2e" \
        --signing-key "$signing_key" --issuer backend 2>/dev/null)"; then
        fail "gateway principal mint failed for friend-auth-sync test"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi

    if ! applicant_principal="$(python3 "$probe" principal \
        --user-id "$applicant" --username "tc-sync-applicant" --tenant "bcs-e2e" \
        --signing-key "$signing_key" --issuer backend 2>/dev/null)"; then
        fail "applicant principal mint failed for friend-auth-sync test"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi

    # Ensure a TC-shaped (bot_id:owner_work_no) protected bot owned by 85020;
    # the colon address is what routes the approve into the TC sync port.
    # user_visibility declares the human-facing visibility at provisioning
    # time so the TC row does not inherit whichever default the backend's
    # schema happens to ship (SQLite says 'protected', MariaDB 'private').
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "X-BCS-Service-Key: e2e-test-key" -H "Content-Type: application/json" \
        -X POST -d '{"name":"TC sync bot","staff_no":"85020","visibility":"protected","user_visibility":"protected"}' \
        "${BCS_API_BASE_URL%/}/admin/bots/${tc_bot}/ensure" 2>/dev/null) || HTTP_STATUS="000"
    if [[ "$HTTP_STATUS" != "200" ]]; then
        fail "ensure TC-shaped bot returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi
    pass "ensure TC-shaped bot returns 200"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Drop any edge left by an earlier run so the create below goes pending
    # again instead of short-circuiting the already-friends path.
    curl -s -o /dev/null -X DELETE \
        -H "X-Mock-User-Id: $applicant" \
        -H "X-Avernet-Principal: $applicant_principal" \
        "${BCS_API_BASE_URL%/}/collaboration/friend-connections/${tc_bot}" 2>/dev/null || true

    curl -s -X POST "$mock_url/control/friend-auth-sync/clear" >/dev/null 2>&1 || true

    # Local auth and the forwarded principal must name the SAME applicant.
    # X-Avernet-Principal alone does not select the local mock caller.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "Content-Type: application/json" \
        -H "X-Mock-User-Id: $applicant" \
        -H "X-Avernet-Principal: $applicant_principal" \
        -X POST -d "{\"to_bot\":\"${tc_bot}\",\"message\":\"tc friend-auth-sync e2e\"}" \
        "${BCS_API_BASE_URL%/}/collaboration/friend-connections/requests" 2>/dev/null) || HTTP_STATUS="000"
    local created_status
    created_status="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("data", {}).get("status", ""))' < "$_RESPONSE_FILE" 2>/dev/null || echo '')"
    if [[ "$HTTP_STATUS" != "200" || "$created_status" != "pending" ]]; then
        fail "human friend request to TC bot returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi
    pass "human friend request to protected TC bot returns 200 (pending)"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # Find the pending request from the explicit applicant's sent list.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "X-Mock-User-Id: $applicant" \
        -H "X-Avernet-Principal: $applicant_principal" \
        "${BCS_API_BASE_URL%/}/collaboration/friend-connections/requests?direction=sent" 2>/dev/null) || HTTP_STATUS="000"
    RESPONSE=$(cat "$_RESPONSE_FILE")
    local request_id
    request_id="$(echo "$RESPONSE" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    data = d.get('data', d)
    items = data.get('items', data) if isinstance(data, dict) else data
    if isinstance(items, list):
        for r in items:
            if r.get('status') == 'pending' and r.get('to_id') == '$tc_bot' and r.get('from_id') == 'human_$applicant':
                print(r.get('request_id', r.get('id', '')))
                break
except Exception:
    pass
" 2>/dev/null || echo '')"
    if [[ -z "$request_id" ]]; then
        fail "pending friend request to $tc_bot not found in sent list"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi

    # The applicant must not approve its own request or trigger a grant sync.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "Content-Type: application/json" \
        -H "X-Mock-User-Id: $applicant" \
        -H "X-Avernet-Principal: $applicant_principal" \
        -X POST -d '{}' \
        "${BCS_API_BASE_URL%/}/collaboration/friend-connections/requests/${request_id}/accept" 2>/dev/null) || HTTP_STATUS="000"
    assert_eq "applicant cannot approve TC friend request" "$HTTP_STATUS" "403"
    if [[ "$HTTP_STATUS" != "403" ]]; then
        return 0
    fi
    local denied_sync_count
    denied_sync_count="$(curl -fsS "$mock_url/control/friend-auth-sync/requests" | \
        python3 -c 'import json,sys; print(len(json.load(sys.stdin)["requests"]))')" || denied_sync_count="unavailable"
    assert_eq "denied applicant approval produces no friend-auth sync" "$denied_sync_count" "0"
    if [[ "$denied_sync_count" != "0" ]]; then
        return 0
    fi

    # Approve as the current owner, with the same identity in local auth and
    # the forwarded backend principal. The outbound copy must be issued by BCS.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' \
        -H "Content-Type: application/json" \
        -H "X-Mock-User-Id: $owner" \
        -H "X-Avernet-Principal: $principal" \
        -X POST -d '{}' \
        "${BCS_API_BASE_URL%/}/collaboration/friend-connections/requests/${request_id}/accept" 2>/dev/null) || HTTP_STATUS="000"
    if [[ "$HTTP_STATUS" != "200" ]]; then
        fail "accept TC friend request returned $HTTP_STATUS: $(cat "$_RESPONSE_FILE")"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return 0
    fi
    pass "accept TC friend request returns 200"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    # The mock's bot-friend-auth endpoint saw exactly one grant, addressed
    # iss=bcs with the approver identity preserved.
    local sync_view sync_check
    sync_view="$(curl -s "$mock_url/control/friend-auth-sync/requests" 2>/dev/null || echo '')"
    sync_check="$(TC_BOT_EXPECTED="$tc_bot" SYNC_VIEW="$sync_view" python3 -c '
import base64, json, os, sys

view = json.loads(os.environ["SYNC_VIEW"])
expected_bot = os.environ["TC_BOT_EXPECTED"]
requests = view.get("requests", [])
problems = []
if len(requests) != 1:
    problems.append(f"expected exactly 1 sync call, saw {len(requests)}")
else:
    record = requests[0]
    body = record.get("body", {})
    bot_id, _, owner_work_no = expected_bot.partition(":")
    for field, want in (
        ("bot_id", bot_id),
        ("owner_work_no", owner_work_no),
        ("action", "grant"),
        ("human_work_no", "153998"),
    ):
        if body.get(field) != want:
            problems.append(f"body.{field}={body.get(field)!r} != {want!r}")
    principal = record.get("principal") or ""
    try:
        payload = principal.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception as error:
        problems.append(f"principal undecodable: {error}")
    else:
        for claim, want in (
            ("iss", "bcs"),
            ("aud", "backend"),
        ):
            if claims.get(claim) != want:
                problems.append(f"claims.{claim}={claims.get(claim)!r} != {want!r}")
        principals = claims.get("principals") or []
        subject = (principals[0].get("subject") or {}) if principals else {}
        if subject.get("id") != "85020":
            problems.append(
                "approver subject id " + repr(subject.get("id")) + " != 85020"
            )
if problems:
    print("; ".join(problems))
    sys.exit(1)
print("ok")
' 2>&1)"
    if [[ "$sync_check" == "ok" ]]; then
        pass "TC grant synced with iss=bcs re-addressed approver principal"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "friend-auth-sync assertion failed: $sync_check"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}
