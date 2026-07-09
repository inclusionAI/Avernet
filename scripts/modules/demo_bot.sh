#!/usr/bin/env bash
# scripts/modules/demo_bot.sh — Default singlebox backend-created demo bot
[[ -n "${_DEMO_BOT_SH_LOADED:-}" ]] && return 0
_DEMO_BOT_SH_LOADED=1

DEMO_BOT_LOG="${LOG_DIR}/demo_bot.log"

demo_bot_defaults() {
    DEMO_BOT_NAME="${SINGLEBOX_DEMO_BOT_NAME:-developer}"
    DEMO_BOT_DESC="${SINGLEBOX_DEMO_BOT_DESC:-Local demo bot for OpenOCB Singlebox}"
    DEMO_ENTITY_ID="${SINGLEBOX_DEMO_ENTITY_ID:-mock-user}"
    DEMO_ENTITY_TYPE="${SINGLEBOX_DEMO_ENTITY_TYPE:-staff}"
    DEMO_ENGINE_TYPE="${SINGLEBOX_DEMO_ENGINE_TYPE:-openclaw}"
    DEMO_BOT_TYPE="${SINGLEBOX_DEMO_BOT_TYPE:-personal}"
    DEMO_TEMPLATE_TYPE="${SINGLEBOX_DEMO_TEMPLATE_TYPE:-normalCC}"
    DEMO_BACKEND_PORT="${BACKEND_PORT:-8888}"
    DEMO_BOT_READY_TIMEOUT_SECONDS="${SINGLEBOX_DEMO_BOT_READY_TIMEOUT_SECONDS:-120}"
    DEMO_BOT_READY_POLL_INTERVAL_SECONDS="${SINGLEBOX_DEMO_BOT_READY_POLL_INTERVAL_SECONDS:-2}"
}

demo_bot_backend_base_url() {
    demo_bot_defaults
    printf 'http://127.0.0.1:%s\n' "${DEMO_BACKEND_PORT}"
}

demo_bot_bcs_base_url() {
    printf 'http://127.0.0.1:%s\n' "${BCS_PORT}"
}

demo_bot_bcs_bot_id() {
    local backend_bot_id="$1"
    demo_bot_defaults
    printf '%s:%s\n' "$backend_bot_id" "$DEMO_ENTITY_ID"
}

demo_bot_json_payload() {
    demo_bot_defaults
    jq -n \
        --arg bot_name "$DEMO_BOT_NAME" \
        --arg bot_desc "$DEMO_BOT_DESC" \
        --arg entity_id "$DEMO_ENTITY_ID" \
        --arg entity_type "$DEMO_ENTITY_TYPE" \
        --arg engine_type "$DEMO_ENGINE_TYPE" \
        --arg bot_type "$DEMO_BOT_TYPE" \
        --arg template_type "$DEMO_TEMPLATE_TYPE" \
        '{
            bot_name: $bot_name,
            bot_desc: $bot_desc,
            entity_id: $entity_id,
            entity_type: $entity_type,
            engine_type: $engine_type,
            bot_type: $bot_type,
            template_type: $template_type
        }'
}

demo_bot_find_existing() {
    demo_bot_defaults
    local base_url response
    base_url="$(demo_bot_backend_base_url)"
    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
            -H "x-user-id: ${DEMO_ENTITY_ID}" \
            "${base_url}/api/bots?user_id=${DEMO_ENTITY_ID}&entity_id=${DEMO_ENTITY_ID}&entity_type=${DEMO_ENTITY_TYPE}&page=1&page_size=100" \
            2>>"${DEMO_BOT_LOG}" || true
    )"
    [ -n "$response" ] || return 0

    printf '%s\n' "$response" | jq -r \
        --arg name "$DEMO_BOT_NAME" \
        --arg entity_id "$DEMO_ENTITY_ID" \
        '
            (.data.bots // .data.items // .data.list // .data.records // .data // [])
            | if type == "array" then . else [] end
            | map(select((.bot_name == $name) and (.entity_id == $entity_id) and ((.is_delete // 0) == 0)))
            | .[0].bot_id // empty
        ' 2>/dev/null || true
}

demo_bot_create() {
    demo_bot_defaults
    local base_url payload response
    base_url="$(demo_bot_backend_base_url)"
    payload="$(demo_bot_json_payload)"
    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 60 -fsS \
            -X POST "${base_url}/api/bots?user_id=${DEMO_ENTITY_ID}" \
            -H "Content-Type: application/json" \
            -H "x-user-id: ${DEMO_ENTITY_ID}" \
            -d "$payload" \
            2>>"${DEMO_BOT_LOG}" || true
    )"
    printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"
    printf '%s\n' "$response" | jq -r '.data.bot_id // .data.bot.bot_id // .bot_id // empty' 2>/dev/null || true
}

demo_bot_wait_ready() {
    local backend_bot_id="$1"
    local base_url response bot_status error_message started_at elapsed
    demo_bot_defaults
    base_url="$(demo_bot_backend_base_url)"
    started_at=$SECONDS

    while true; do
        response="$(
            curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
                -H "x-user-id: ${DEMO_ENTITY_ID}" \
                "${base_url}/api/bots/${backend_bot_id}/status?owner_id=${DEMO_ENTITY_ID}" \
                2>>"${DEMO_BOT_LOG}" || true
        )"

        if [ -n "$response" ]; then
            printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"
            if printf '%s\n' "$response" | jq -e '.success == true and .data.is_ready == true' >/dev/null 2>&1; then
                return 0
            fi

            bot_status="$(printf '%s\n' "$response" | jq -r '.data.bot_status // empty' 2>/dev/null || true)"
            error_message="$(printf '%s\n' "$response" | jq -r '.data.error_message // empty' 2>/dev/null || true)"
            if [ "$bot_status" = "FAILED" ] || [ -n "$error_message" ]; then
                log_error "Demo bot backend status failed: bot_id=${backend_bot_id}, status=${bot_status:-unknown}, error=${error_message:-none}"
                return 1
            fi
        fi

        elapsed=$((SECONDS - started_at))
        if [ "$elapsed" -ge "$DEMO_BOT_READY_TIMEOUT_SECONDS" ]; then
            log_error "Timed out waiting for demo bot backend readiness: bot_id=${backend_bot_id}"
            return 1
        fi
        sleep "$DEMO_BOT_READY_POLL_INTERVAL_SECONDS"
    done
}

demo_bot_bcs_cli() {
    if command -v bcs-cli >/dev/null 2>&1; then
        command -v bcs-cli
        return 0
    fi
    if [ -x "${BCS_DIR}/target/debug/bcs-cli" ]; then
        printf '%s\n' "${BCS_DIR}/target/debug/bcs-cli"
        return 0
    fi
    return 1
}

demo_bot_connect_bcs() {
    local backend_bot_id="$1"
    local bcs_bot_id base_url payload response
    bcs_bot_id="$(demo_bot_bcs_bot_id "$backend_bot_id")"
    base_url="$(demo_bot_bcs_base_url)"
    payload="$(jq -n --arg bot_id "$bcs_bot_id" '{bot_id: $bot_id, protocol_version: 2}')"

    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
            -X POST "${base_url}/bots/connect" \
            -H "Content-Type: application/json" \
            -d "$payload" \
            2>>"${DEMO_BOT_LOG}" || true
    )"
    printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"

    if printf '%s\n' "$response" | jq -e --arg bot_id "$bcs_bot_id" '.bot_uuid == $bot_id' >/dev/null 2>&1; then
        return 0
    fi
    if printf '%s\n' "$response" | grep -qi 'already'; then
        return 0
    fi
    return 1
}

demo_bot_admin_onboard_bcs() {
    local backend_bot_id="$1"
    local bcs_bot_id base_url payload response
    bcs_bot_id="$(demo_bot_bcs_bot_id "$backend_bot_id")"
    base_url="$(demo_bot_bcs_base_url)"
    payload="$(
        jq -n \
            --arg bot_id "$bcs_bot_id" \
            --arg name "$DEMO_BOT_NAME" \
            --arg summary "$DEMO_BOT_DESC" \
            '{bot_id: $bot_id, name: $name, summary: $summary, hidden: true}'
    )"

    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
            -X POST "${base_url}/admin/bots/onboard" \
            -H "Content-Type: application/json" \
            -d "$payload" \
            2>>"${DEMO_BOT_LOG}" || true
    )"
    printf '%s\n' "$response" >> "${DEMO_BOT_LOG}"
    printf '%s\n' "$response" | jq -e '.onboarded == true' >/dev/null 2>&1
}

demo_bot_verify_bcn() {
    local backend_bot_id="$1"
    local bcs_bot_id cli
    bcs_bot_id="$(demo_bot_bcs_bot_id "$backend_bot_id")"
    cli="$(demo_bot_bcs_cli)" || {
        log_error "bcs-cli not found; run: ./scripts/singlebox.sh setup bcs"
        return 1
    }

    "$cli" --url "http://127.0.0.1:${BCS_PORT}" get "$bcs_bot_id" >> "${DEMO_BOT_LOG}" 2>&1
}

demo_bot_has_expected_bcn_metadata() {
    local bcs_bot_id="$1"
    local log_tail
    log_tail="$(tail -n 20 "${DEMO_BOT_LOG}" 2>/dev/null || true)"

    printf '%s\n' "$log_tail" | grep -F "Bot: ${bcs_bot_id}" >/dev/null 2>&1 || return 1
    printf '%s\n' "$log_tail" | grep -F "Name: ${DEMO_BOT_NAME}" >/dev/null 2>&1 || return 1
    printf '%s\n' "$log_tail" | grep -F "Summary: ${DEMO_BOT_DESC}" >/dev/null 2>&1 || return 1
}

demo_bot_ensure_bcn() {
    local backend_bot_id="$1"
    local bcs_bot_id
    bcs_bot_id="$(demo_bot_bcs_bot_id "$backend_bot_id")"

    if demo_bot_verify_bcn "$backend_bot_id"; then
        if demo_bot_has_expected_bcn_metadata "$bcs_bot_id"; then
            return 0
        fi
    fi

    log_info "Registering demo bot in local BCS: ${bcs_bot_id}"
    demo_bot_admin_onboard_bcs "$backend_bot_id" || return 1
    demo_bot_verify_bcn "$backend_bot_id" && demo_bot_has_expected_bcn_metadata "$bcs_bot_id"
}

demo_bot_start() {
    mkdir -p "${LOG_DIR}"
    : > "${DEMO_BOT_LOG}"
    demo_bot_defaults

    backend_ready || {
        log_error "Backend is not ready; cannot create demo bot"
        return 1
    }
    bcs_ready || {
        log_error "BCS is not ready; cannot verify demo bot onboard"
        return 1
    }

    local backend_bot_id
    backend_bot_id="$(demo_bot_find_existing)"
    if [ -n "$backend_bot_id" ]; then
        log_info "Reusing existing singlebox demo bot: ${backend_bot_id}"
    else
        log_info "Creating singlebox demo bot through backend: ${DEMO_BOT_NAME}"
        backend_bot_id="$(demo_bot_create)"
    fi

    if [ -z "$backend_bot_id" ]; then
        log_error "Failed to create or locate the singlebox demo bot. Check ${DEMO_BOT_LOG}"
        return 1
    fi

    if ! demo_bot_wait_ready "$backend_bot_id"; then
        log_error "Demo bot backend did not become ready: ${backend_bot_id}. Check ${DEMO_BOT_LOG}"
        return 1
    fi

    if ! demo_bot_ensure_bcn "$backend_bot_id"; then
        log_error "Failed to verify demo bot in local BCS/BCN: $(demo_bot_bcs_bot_id "$backend_bot_id"). Check ${DEMO_BOT_LOG}"
        return 1
    fi

    log_info "Singlebox demo bot ready: $(demo_bot_bcs_bot_id "$backend_bot_id")"
}

demo_bot_ready() {
    demo_bot_defaults
    local backend_bot_id
    backend_bot_id="$(demo_bot_find_existing)"
    [ -n "$backend_bot_id" ] && demo_bot_verify_bcn "$backend_bot_id"
}

demo_bot_prereqs() {
    local has_error=false
    if check_command jq; then
        prereq_ok "jq: $(command -v jq)"
    else
        prereq_error "jq not found. Install jq before starting all."
        has_error=true
    fi
    if check_command curl; then
        prereq_ok "curl: $(command -v curl)"
    else
        prereq_error "curl not found."
        has_error=true
    fi
    if demo_bot_bcs_cli >/dev/null 2>&1; then
        prereq_ok "bcs-cli: $(demo_bot_bcs_cli)"
    else
        prereq_error "bcs-cli not found. Run: ./scripts/singlebox.sh setup bcs"
        has_error=true
    fi
    [ "$has_error" = false ]
}

demo_bot_status() {
    demo_bot_defaults
    local backend_bot_id
    backend_bot_id="$(demo_bot_find_existing)"
    if [ -n "$backend_bot_id" ] && demo_bot_verify_bcn "$backend_bot_id"; then
        echo "  Demo Bot:  Ready ($(demo_bot_bcs_bot_id "$backend_bot_id"))"
    else
        echo "  Demo Bot:  Not ready"
    fi
}

demo_bot_help() {
    echo "demo_bot - backend-created mock demo bot onboarded to local BCS"
}
