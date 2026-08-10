#!/usr/bin/env bash
# scripts/modules/claude_bots.sh — Backend-created Claude Code normalCC bots
[[ -n "${_CLAUDE_BOTS_SH_LOADED:-}" ]] && return 0
_CLAUDE_BOTS_SH_LOADED=1

CLAUDE_BOTS_LOG="${LOG_DIR}/claude_bots.log"

claude_bots_entity_id() {
    local config_path
    config_path="$(claude_bots_config_path)"
    jq -r '.entity_id // "mock-user"' "$config_path"
}

claude_bots_entity_type() {
    local config_path
    config_path="$(claude_bots_config_path)"
    jq -r '.entity_type // "staff"' "$config_path"
}

claude_bots_backend_base_url() {
    printf 'http://127.0.0.1:%s\n' "${BACKEND_PORT:-8888}"
}

claude_bots_find_existing() {
    local name="$1" entity_id="$2" response
    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
            -H "x-user-id: ${entity_id}" \
            "$(claude_bots_backend_base_url)/api/bots?user_id=${entity_id}&entity_id=${entity_id}&entity_type=$(claude_bots_entity_type)&page=1&page_size=100" \
            2>>"${CLAUDE_BOTS_LOG}" || true
    )"
    [ -n "$response" ] || return 0
    printf '%s\n' "$response" | jq -r --arg name "$name" --arg entity "$entity_id" '
        (.data.bots // .data.items // .data.list // .data.records // .data // [])
        | if type == "array" then . else [] end
        | map(select(.bot_name == $name and .entity_id == $entity and ((.is_delete // 0) == 0)))
        | .[0].bot_id // empty
    ' 2>/dev/null || true
}

claude_bots_create_payload() {
    local role="$1" name="$2" description="$3" port="$4" workspace="$5" model="$6" entity_id="$7"
    local relay_url="ws://127.0.0.1:${port}"
    jq -n \
        --arg bot_name "$name" \
        --arg bot_desc "$description" \
        --arg entity_id "$entity_id" \
        --arg entity_type "$(claude_bots_entity_type)" \
        --arg relay_url "$relay_url" \
        --arg role "$role" \
        --arg workspace "$workspace" \
        --arg model "$model" \
        '{
            bot_name: $bot_name,
            bot_desc: $bot_desc,
            entity_id: $entity_id,
            entity_type: $entity_type,
            engine_type: "claude_code",
            bot_type: "personal",
            template_type: "normalCC",
            template_config: {
                singlebox_claude: {
                    relay_url: $relay_url,
                    role: $role,
                    workspace: $workspace,
                    model: $model
                }
            }
        }'
}

claude_bots_create() {
    local role="$1" name="$2" description="$3" port="$4" workspace="$5" model="$6" entity_id="$7"
    local payload response
    payload="$(claude_bots_create_payload "$role" "$name" "$description" "$port" "$workspace" "$model" "$entity_id")"
    response="$(
        curl --noproxy '*' --connect-timeout 2 --max-time 90 -fsS \
            -X POST "$(claude_bots_backend_base_url)/api/bots?user_id=${entity_id}" \
            -H 'Content-Type: application/json' \
            -H "x-user-id: ${entity_id}" \
            -d "$payload" 2>>"${CLAUDE_BOTS_LOG}" || true
    )"
    # Record only envelope metadata; template config can contain user paths.
    printf '%s\n' "$response" | jq -c '{success: (.success // false), has_bot_id: ((.data.bot_id // .data.bot.bot_id // .bot_id // "") != "")}' >> "$CLAUDE_BOTS_LOG" 2>/dev/null || true
    printf '%s\n' "$response" | jq -r '.data.bot_id // .data.bot.bot_id // .bot_id // empty' 2>/dev/null || true
}

claude_bots_wait_ready() {
    local bot_id="$1" entity_id="$2" started_at="$SECONDS" response status error
    while true; do
        response="$(
            curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
                -H "x-user-id: ${entity_id}" \
                "$(claude_bots_backend_base_url)/api/bots/${bot_id}/status?owner_id=${entity_id}" \
                2>>"${CLAUDE_BOTS_LOG}" || true
        )"
        if printf '%s\n' "$response" | jq -e '.success == true and .data.is_ready == true' >/dev/null 2>&1; then
            return 0
        fi
        status="$(printf '%s\n' "$response" | jq -r '.data.bot_status // empty' 2>/dev/null || true)"
        error="$(printf '%s\n' "$response" | jq -r '.data.error_message // empty' 2>/dev/null || true)"
        if [ "$status" = "FAILED" ] || [ -n "$error" ]; then
            log_error "Claude bot did not become ready: role binding=${bot_id} status=${status:-unknown}"
            return 1
        fi
        if [ $((SECONDS - started_at)) -ge "${CLAUDE_BOT_READY_TIMEOUT_SECONDS:-180}" ]; then
            log_error "Timed out waiting for Claude bot: ${bot_id}"
            return 1
        fi
        sleep 2
    done
}

claude_bots_write_state() {
    local json="$1"
    umask 077
    printf '%s\n' "$json" > "$CLAUDE_BOTS_STATE_FILE"
    chmod 600 "$CLAUDE_BOTS_STATE_FILE"
}

claude_bots_setup() {
    claude_bots_enabled || return 0
    claude_bots_validate_config || return 1
    check_command jq || { log_error "jq is required for Claude bot provisioning"; return 1; }
}

claude_bots_prereqs() {
    claude_bots_enabled || return 0
    claude_bots_setup
}

claude_bots_start() {
    claude_bots_enabled || return 0
    claude_bots_setup || return 1
    backend_ready || { log_error "Backend is not ready; cannot create Claude bots"; return 1; }
    baas_ready || { log_error "BAAS is not ready; cannot create Claude bots"; return 1; }
    mkdir -p "$LOG_DIR"
    : > "$CLAUDE_BOTS_LOG"

    local entity_id role name description port config_dir workspace model bot_id items='[]'
    entity_id="$(claude_bots_entity_id)"
    while IFS=$'\t' read -r role name description port config_dir workspace model; do
        bot_id="$(claude_bots_find_existing "$name" "$entity_id")"
        if [ -n "$bot_id" ]; then
            log_info "Reusing Claude ${role} bot: ${bot_id}"
        else
            log_info "Creating Claude ${role} normalCC bot through Backend"
            bot_id="$(claude_bots_create "$role" "$name" "$description" "$port" "$workspace" "$model" "$entity_id")"
        fi
        if [ -z "$bot_id" ]; then
            log_error "Failed to create or locate Claude ${role} bot; check ${CLAUDE_BOTS_LOG}"
            return 1
        fi
        claude_bots_wait_ready "$bot_id" "$entity_id" || return 1
        items="$(jq -c --arg role "$role" --arg bot_id "$bot_id" --arg name "$name" --arg port "$port" \
            '. + [{role: $role, bot_id: $bot_id, name: $name, relay_port: ($port | tonumber)}]' <<< "$items")"
    done < <(claude_bots_entries)
    claude_bots_write_state "$(jq -n --arg entity_id "$entity_id" --arg entity_type "$(claude_bots_entity_type)" --argjson bots "$items" '{entity_id: $entity_id, entity_type: $entity_type, bots: $bots}')"
    log_info "Started three Claude Code normalCC bots with isolated relay endpoints"
}

claude_bots_stop() {
    # BaaS owns the adapter PIDs and is stopped later in the reverse topology.
    # Keep the state until the Provider bridge has already stopped and report
    # completion without deleting Backend-owned bot records.
    claude_bots_enabled || return 0
    log_info "Claude bot adapters remain managed by BAAS until BAAS stop"
}

claude_bots_ready() {
    claude_bots_enabled || return 0
    [ -f "$CLAUDE_BOTS_STATE_FILE" ] || return 1
    jq -e '.bots | length == 3' "$CLAUDE_BOTS_STATE_FILE" >/dev/null 2>&1
}

claude_bots_status() {
    claude_bots_enabled || return 0
    if [ ! -f "$CLAUDE_BOTS_STATE_FILE" ]; then
        echo "  Claude bots: Not ready"
        return 0
    fi
    echo "  Claude bots (3):"
    jq -r '.bots[] | "    \(.role): Started (\(.bot_id); relay \(.relay_port))"' "$CLAUDE_BOTS_STATE_FILE" 2>/dev/null || {
        echo "  Claude bots: state unreadable"
        return 0
    }
    echo "  Claude adapters (3):"
    jq -r '.bots[] | "    \(.role): BAAS-managed (relay \(.relay_port))"' "$CLAUDE_BOTS_STATE_FILE"
}

claude_bots_help() {
    echo "claude_bots - three Backend-created Claude Code normalCC bots (mixed mode only)"
}
