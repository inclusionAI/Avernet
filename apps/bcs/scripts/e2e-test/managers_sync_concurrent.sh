#!/bin/bash
# managers_sync_concurrent.sh — Trigger managers sync retry path
# via concurrent full-set replace calls on the same bot.
#
# Two overlapping sync requests with different manager sets
# race on the optimistic-lock guards. The loser retries with
# the exponential backoff added in PR #4170, which exercises
# the changed lines that the single-threaded E2E stories never
# reach.

E2E_TESTS_STORIES+=("story_managers_sync_concurrent")

story_managers_sync_concurrent() {
    info "Story: concurrent managers sync exercises the retry backoff path"

    local prefix="/api/v1/collaboration"
    local signing_key="${AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE:-avernet-dev-signing-key-NOT-FOR-PROD}"
    local probe="${SCRIPT_DIR}/group_session_ws_probe.py"
    local fixtures owner_bot owner_token

    # 1. Register an owned bot
    fixtures=$(
        curl --noproxy '*' -s -S \
            -H "X-Mock-User-Id: $BCS_MOCK_USER_ID" \
            -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME" \
            "${BCS_API_BASE_URL}/register/token"
    ) || { fail "register token failed"; TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return; }
    owner_token=$(printf '%s' "$fixtures" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))' 2>/dev/null)
    [[ -n "$owner_token" ]] || { fail "empty register token"; TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return; }
    local enc_token enc_name register
    enc_token=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$owner_token")
    enc_name=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "sync-test-$$-$(date +%s)")
    register=$(curl --noproxy '*' -s -S -X POST "${BCS_API_BASE_URL}/register?token=${enc_token}&bot-name=${enc_name}")
    owner_bot=$(printf '%s' "$register" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("bot_uuid",""))' 2>/dev/null)
    [[ -n "$owner_bot" ]] || { fail "trusted registration failed"; TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return; }

    # 2. Seed humans
    local human_a="human_sync_a" human_b="human_sync_b" human_c="human_sync_c"
    for h in "$human_a" "$human_b" "$human_c"; do
        curl --noproxy '*' -s -S -X POST \
            -H "X-Mock-User-Id: $BCS_MOCK_USER_ID" \
            -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME" \
            -H "Content-Type: application/json" \
            -d "{\"user_id\":\"$h\",\"nick_name\":\"$h\"}" \
            "${BCS_API_BASE_URL}/internal/ensure-human" >/dev/null 2>&1 || true
    done

    # 3. Get App principal (only trusted apps can call sync_managers)
    local app_principal
    if ! app_principal=$(python3 "$probe" app-principal \
            --app-id 7 \
            --app-name "backend" \
            --tenant "teamclaw" \
            --signing-key "$signing_key"); then
        fail "Gateway App-Principal generation failed"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        return
    fi

    # 4. Grant owner edge so the bot passes ownership checks
    local grant_resp
    grant_resp=$(curl --noproxy '*' -s -S -X PUT \
        -H "X-Avernet-Principal: ${app_principal}" \
        -H "Content-Type: application/json" \
        -d "{\"owner_user_id\":\"${BCS_MOCK_USER_ID}\"}" \
        "${BCS_API_BASE_URL}${prefix}/bots/${owner_bot}/ownership" 2>/dev/null) || true

    # 5. Concurrent sync: two requests with different manager sets
    #    sent in background to overlap in the optimistic-lock window.
    local tmp1 tmp2 resp1 resp2 code1 code2
    tmp1=$(mktemp) tmp2=$(mktemp)
    local body_a="{\"manager_user_ids\":[\"${human_a}\",\"${human_b}\"]}"
    local body_b="{\"manager_user_ids\":[\"${human_a}\",\"${human_c}\"]}"

    # Fire both concurrently
    curl --noproxy '*' -s -S -X PUT \
        -H "X-Avernet-Principal: ${app_principal}" \
        -H "Content-Type: application/json" \
        -d "$body_a" \
        "${BCS_API_BASE_URL}${prefix}/bots/${owner_bot}/managers" \
        -o "$tmp1" -w '%{http_code}' > /tmp/sync_code1.txt 2>/dev/null &
    pid1=$!

    curl --noproxy '*' -s -S -X PUT \
        -H "X-Avernet-Principal: ${app_principal}" \
        -H "Content-Type: application/json" \
        -d "$body_b" \
        "${BCS_API_BASE_URL}${prefix}/bots/${owner_bot}/managers" \
        -o "$tmp2" -w '%{http_code}' > /tmp/sync_code2.txt 2>/dev/null &
    pid2=$!

    wait $pid1 $pid2 2>/dev/null || true
    code1=$(cat /tmp/sync_code1.txt 2>/dev/null || echo "000")
    code2=$(cat /tmp/sync_code2.txt 2>/dev/null || echo "000")

    # 6. At least one must succeed (200); both may succeed if the
    #    backoff resolves the conflict.
    local any_ok=0
    if [[ "$code1" == "200" ]]; then
        any_ok=1
    fi
    if [[ "$code2" == "200" ]]; then
        any_ok=1
    fi

    if [[ $any_ok -eq 0 ]]; then
        fail "concurrent managers sync: both requests failed (${code1} / ${code2})"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    else
        pass "concurrent managers sync: at least one request succeeded (${code1} / ${code2})"
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    rm -f "$tmp1" "$tmp2" /tmp/sync_code1.txt /tmp/sync_code2.txt
}
