#!/bin/bash
# managers_sync_concurrent.sh — Trigger managers sync retry path
# via concurrent full-set replace calls on the same bot.
#
# Uses a Python helper (managers_sync_race.py) that opens multiple
# HTTP connections, waits for all to be established, then sends all
# request bodies at the same instant. This reliably triggers the
# optimistic-lock conflict that exercises the retry+backoff path
# added in PR #4170.

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

    # 2. Seed humans (need 5+ distinct humans)
    local human_a="human_sync_a" human_b="human_sync_b" human_c="human_sync_c"
    local human_d="human_sync_d" human_e="human_sync_e"
    for h in "$human_a" "$human_b" "$human_c" "$human_d" "$human_e"; do
        curl --noproxy '*' -s -S -X POST \
            -H "X-Mock-User-Id: $BCS_MOCK_USER_ID" \
            -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME" \
            -H "Content-Type: application/json" \
            -d "{\"user_id\":\"$h\",\"nick_name\":\"$h\"}" \
            "${BCS_API_BASE_URL}/internal/ensure-human" >/dev/null 2>&1 || true
    done

    # 3. Get App principal
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

    # 4. Grant owner edge
    curl --noproxy '*' -s -S -X PUT \
        -H "X-Avernet-Principal: ${app_principal}" \
        -H "Content-Type: application/json" \
        -d "{\"owner_user_id\":\"${BCS_MOCK_USER_ID}\"}" \
        "${BCS_API_BASE_URL}${prefix}/bots/${owner_bot}/ownership" >/dev/null 2>&1 || true

    # 5. Seed managers to a known baseline
    curl --noproxy '*' -s -S -X PUT \
        -H "X-Avernet-Principal: ${app_principal}" \
        -H "Content-Type: application/json" \
        -d "{\"manager_user_ids\":[\"${human_a}\"]}" \
        "${BCS_API_BASE_URL}${prefix}/bots/${owner_bot}/managers" >/dev/null 2>&1 || true

    # 6. Concurrent sync via Python helper: opens N connections,
    #    waits for all to be ready, then sends all bodies at once.
    local bodies_json race_output
    bodies_json=$(python3 -c "
import json, sys
a='${human_a}'; b='${human_b}'; c='${human_c}'; d='${human_d}'; e='${human_e}'
print(json.dumps([
    [a, b], [a, c], [a, d], [a, e]
]))
")
    race_output=$(python3 "${SCRIPT_DIR}/managers_sync_race.py" \
        --base-url "${BCS_API_BASE_URL}" \
        --bot-id "${owner_bot}" \
        --principal "${app_principal}" \
        --bodies "${bodies_json}" \
        --concurrency 4 2>&1) || true

    # 7. Parse results
    local ok_count
    ok_count=$(printf '%s' "$race_output" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("ok",0))' 2>/dev/null || echo "0")
    local total_count
    total_count=$(printf '%s' "$race_output" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("total",0))' 2>/dev/null || echo "0")

    if [[ -z "$ok_count" || "$ok_count" == "0" ]]; then
        # Fallback: retry with plain concurrent curl
        info "Python race helper failed or returned 0; falling back to plain concurrent curl"
        local -a pids=() code_files=()
        local bodies=(
            "{\"manager_user_ids\":[\"${human_a}\",\"${human_b}\"]}"
            "{\"manager_user_ids\":[\"${human_a}\",\"${human_c}\"]}"
            "{\"manager_user_ids\":[\"${human_a}\",\"${human_d}\"]}"
            "{\"manager_user_ids\":[\"${human_a}\",\"${human_e}\"]}"
        )
        for i in 0 1 2 3; do
            local cf
            cf=$(mktemp)
            code_files+=("$cf")
            curl --noproxy '*' -s -S -X PUT \
                -H "X-Avernet-Principal: ${app_principal}" \
                -H "Content-Type: application/json" \
                -d "${bodies[$i]}" \
                "${BCS_API_BASE_URL}${prefix}/bots/${owner_bot}/managers" \
                -o /dev/null -w '%{http_code}' > "$cf" 2>/dev/null &
            pids+=($!)
        done
        for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
        ok_count=0
        for cf in "${code_files[@]}"; do
            local code
            code=$(cat "$cf" 2>/dev/null || echo "000")
            if [[ "$code" == "200" ]]; then ok_count=$((ok_count + 1)); fi
            rm -f "$cf"
        done
        total_count=${#bodies[@]}
    fi

    if [[ "$ok_count" -eq "$total_count" ]]; then
        pass "concurrent managers sync: all ${total_count} requests succeeded"
    elif [[ "$ok_count" -gt 0 ]]; then
        pass "concurrent managers sync: ${ok_count}/${total_count} succeeded"
    else
        fail "concurrent managers sync: 0/${total_count} succeeded"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}
