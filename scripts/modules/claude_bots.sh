#!/usr/bin/env bash
# scripts/modules/claude_bots.sh — one Backend-created Claude Code normalCC bot
[[ -n "${_CLAUDE_BOTS_SH_LOADED:-}" ]] && return 0
_CLAUDE_BOTS_SH_LOADED=1

CLAUDE_BOTS_LOG="${LOG_DIR}/claude_bots.merchant_hybrid.log"
CLAUDE_BOTS_STATE_FILE="${DEP_DIR}/claude_bots.merchant_hybrid.state.json"

claude_bots_enabled() {
    claude_profile_enabled
}

claude_bots_backend_url() {
    printf 'http://127.0.0.1:%s\n' "${BACKEND_PORT:-8888}"
}

claude_bots_find_existing() {
    local name="$1" entity_id="$2" response
    response="$(curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
        -H "x-user-id: ${entity_id}" \
        "$(claude_bots_backend_url)/api/bots?user_id=${entity_id}&entity_id=${entity_id}&entity_type=$(claude_profile_entity_type)&page=1&page_size=100" \
        2>>"$CLAUDE_BOTS_LOG" || true)"
    [ -n "$response" ] || return 0
    jq -r --arg name "$name" --arg entity "$entity_id" '
        (.data.bots // .data.items // .data.list // .data.records // .data // [])
        | if type == "array" then . else [] end
        | map(select(.bot_name == $name and .entity_id == $entity and ((.is_delete // 0) == 0)))
        | .[0].bot_id // empty
    ' <<< "$response" 2>/dev/null || true
}

claude_bots_create() {
    local name="$1" summary="$2" entity_id="$3" payload response
    payload="$(jq -n --arg bot_name "$name" --arg bot_desc "$summary" --arg entity_id "$entity_id" --arg entity_type "$(claude_profile_entity_type)" '
        {bot_name: $bot_name, bot_desc: $bot_desc, entity_id: $entity_id, entity_type: $entity_type,
         engine_type: "claude_code", bot_type: "personal", template_type: "normalCC"}')"
    response="$(curl --noproxy '*' --connect-timeout 2 --max-time 90 -fsS -X POST \
        "$(claude_bots_backend_url)/api/bots?user_id=${entity_id}" \
        -H 'Content-Type: application/json' -H "x-user-id: ${entity_id}" -d "$payload" 2>>"$CLAUDE_BOTS_LOG" || true)"
    jq -c '{success: (.success // false), has_bot_id: ((.data.bot_id // .data.bot.bot_id // .bot_id // "") != "")}' <<< "$response" >> "$CLAUDE_BOTS_LOG" 2>/dev/null || true
    jq -r '.data.bot_id // .data.bot.bot_id // .bot_id // empty' <<< "$response" 2>/dev/null || true
}

claude_bots_wait_ready() {
    local bot_id="$1" entity_id="$2" started_at="$SECONDS" response status error
    while true; do
        response="$(curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS -H "x-user-id: ${entity_id}" \
            "$(claude_bots_backend_url)/api/bots/${bot_id}/status?owner_id=${entity_id}" 2>>"$CLAUDE_BOTS_LOG" || true)"
        if jq -e '.success == true and .data.is_ready == true' <<< "$response" >/dev/null 2>&1; then return 0; fi
        status="$(jq -r '.data.bot_status // empty' <<< "$response" 2>/dev/null || true)"
        error="$(jq -r '.data.error_message // empty' <<< "$response" 2>/dev/null || true)"
        if [ "$status" = FAILED ] || [ -n "$error" ]; then
            log_error "Claude bot did not become ready: binding=${bot_id} status=${status:-unknown}"
            return 1
        fi
        if [ $((SECONDS - started_at)) -ge "${CLAUDE_BOT_READY_TIMEOUT_SECONDS:-180}" ]; then
            log_error "Timed out waiting for Claude bot: ${bot_id}"
            return 1
        fi
        sleep 2
    done
}

claude_bots_setup() {
    claude_bots_enabled || return 0
    claude_profile_validate_config || return 1
    check_command jq || { log_error "jq is required for Claude bot provisioning"; return 1; }
}

claude_bots_prereqs() {
    claude_bots_enabled || return 0
    claude_bots_setup
}

claude_bots_start() {
    claude_bots_enabled || return 0
    claude_bots_setup || return 1
    backend_ready || { log_error "Backend is not ready; cannot create Claude bot"; return 1; }
    baas_ready || { log_error "BAAS is not ready; cannot create Claude bot"; return 1; }
    mkdir -p "$LOG_DIR"
    : > "$CLAUDE_BOTS_LOG"
    local role name summary port config_dir workspace model prompt_file permission entity_id bot_id
    IFS=$'\x1f' read -r role name summary port config_dir workspace model prompt_file permission < <(claude_profile_entries)
    entity_id="$(claude_profile_entity_id)"
    bot_id="$(claude_bots_find_existing "$name" "$entity_id")"
    if [ -z "$bot_id" ]; then
        log_info "Creating Claude ${role} normalCC bot through Backend"
        bot_id="$(claude_bots_create "$name" "$summary" "$entity_id")"
    fi
    [ -n "$bot_id" ] || { log_error "Failed to create Claude bot; check ${CLAUDE_BOTS_LOG}"; return 1; }
    claude_bots_wait_ready "$bot_id" "$entity_id" || return 1
    umask 077
    jq -n --arg entity_id "$entity_id" --arg entity_type "$(claude_profile_entity_type)" \
        --arg bots_profile_dir "${BOTS_PROFILE_DIR:-}" --arg claude_profile_dir "${CLAUDE_PROFILE_DIR:-}" \
        --arg claude_config_dir "$config_dir" --arg workspace "$workspace" \
        --arg role "$role" --arg bot_id "$bot_id" --arg name "$name" --argjson relay_port "$port" \
        '{entity_id: $entity_id, entity_type: $entity_type, bots_profile_dir: $bots_profile_dir,
          claude_profile_dir: $claude_profile_dir, claude_config_dir: $claude_config_dir,
          workspace: $workspace,
          bots: [{role: $role, bot_id: $bot_id, name: $name, relay_port: $relay_port}]}' > "$CLAUDE_BOTS_STATE_FILE"
    chmod 600 "$CLAUDE_BOTS_STATE_FILE"
    log_info "Started one Claude Code normalCC bot on relay ${port}"

    # Register the Claude bot in bcsfuse so it can participate in fusion.
    _claude_bots_register_bcsfuse_fusion "$bot_id" "$name" "$summary"
}

# Resolve BCSFUSE_AUTH_TOKEN from the bcsfuse env file or current environment.
# Tolerates both `BCSFUSE_AUTH_TOKEN=...` and `export BCSFUSE_AUTH_TOKEN=...`.
_claude_bots_bcsfuse_auth_token() {
    local token="${BCSFUSE_AUTH_TOKEN:-}"
    if [ -n "$token" ]; then
        printf '%s\n' "$token"
        return 0
    fi
    local env_file="${BCSFUSE_ENV_FILE:-${BCSFUSE_DIR:-${PROJECT_ROOT}/src/bcsfuse}/.runtime/env/.env.local}"
    if [ -f "$env_file" ]; then
        token="$(grep -E '^(export )?BCSFUSE_AUTH_TOKEN=' "$env_file" 2>/dev/null | head -1 | sed -E 's/^(export )?BCSFUSE_AUTH_TOKEN="?([^"]*)"?$/\2/')"
    fi
    if [ -n "$token" ]; then
        log_info "Resolved BCSFUSE_AUTH_TOKEN from ${env_file}"
        printf '%s\n' "$token"
        return 0
    fi
    log_warn "BCSFUSE_AUTH_TOKEN not found in env var or ${env_file} (file exists: $([ -f "$env_file" ] && echo yes || echo no))"
    return 0
}

# Register the Claude Code bot as a bcsfuse worker and enable fusion.
# Uses the backend bot_id as the bcsfuse worker_id.
_claude_bots_register_bcsfuse_fusion() {
    local bot_id="$1"
    local name="$2"
    local summary="$3"

    if [ -z "$bot_id" ]; then
        return 0
    fi

    local bcsfuse_url="http://127.0.0.1:${BCSFUSE_PORT:-8765}"
    local auth_token
    auth_token="$(_claude_bots_bcsfuse_auth_token)"
    if [ -z "$auth_token" ]; then
        log_warn "BCSFUSE_AUTH_TOKEN not available; skipping Claude bot bcsfuse fusion registration"
        return 0
    fi

    # Wait until bcsfuse health endpoint is reachable.
    local attempt=0
    while [ "$attempt" -lt 10 ]; do
        if curl -sf --max-time 2 "${bcsfuse_url}/health" >/dev/null 2>&1; then
            break
        fi
        sleep 1
        attempt=$((attempt + 1))
    done
    if [ "$attempt" -ge 10 ]; then
        log_warn "bcsfuse health check not passing; skipping Claude bot fusion registration"
        return 0
    fi

    local domains_csv skills_csv
    domains_csv="$(claude_profile_first_bot_domains 2>/dev/null || true)"
    skills_csv="$(claude_profile_first_bot_skills 2>/dev/null || true)"

    # Convert comma-separated values to JSON arrays.
    local domains_json skills_json
    if [ -n "$domains_csv" ]; then
        domains_json="$(printf '%s\n' "$domains_csv" | tr ',' '\n' | jq -R . | jq -s .)" || domains_json="[]"
    else
        domains_json="[]"
    fi
    if [ -n "$skills_csv" ]; then
        skills_json="$(printf '%s\n' "$skills_csv" | tr ',' '\n' | jq -R . | jq -s .)" || skills_json="[]"
    else
        skills_json="[]"
    fi

    local payload response status body
    payload="$(jq -n \
        --arg worker_id "$bot_id" \
        --arg name "$name" \
        --arg description "$summary" \
        --argjson skills "$skills_json" \
        --argjson domains "$domains_json" \
        '{worker_id: $worker_id, name: $name, description: $description, skills: $skills, domains: $domains, is_public: true}')"

    response="$(curl -s -w '\n%{http_code}' -X POST "${bcsfuse_url}/v1/admin/workers" \
        -H 'Content-Type: application/json' \
        -H "Authorization: Bearer ${auth_token}" \
        -d "$payload" 2>/dev/null || true)"
    status="$(printf '%s\n' "$response" | tail -1)"
    body="$(printf '%s\n' "$response" | sed '$d')"

    if [ "$status" != "200" ] && [ "$status" != "201" ]; then
        log_warn "Failed to register Claude bot in bcsfuse: HTTP ${status}: ${body}"
        return 0
    fi
    log_info "Registered Claude bot as bcsfuse worker: ${bot_id} (${name})"

    response="$(curl -s -w '\n%{http_code}' -X PUT "${bcsfuse_url}/v1/workers/${bot_id}/config" \
        -H 'Content-Type: application/json' \
        -H "Authorization: Bearer ${auth_token}" \
        -d '{"fusion_enable": true}' 2>/dev/null || true)"
    status="$(printf '%s\n' "$response" | tail -1)"
    if [ "$status" = "200" ]; then
        log_info "Enabled bcsfuse fusion for Claude bot: ${name}"
    else
        log_warn "Failed to enable bcsfuse fusion for Claude bot: HTTP ${status}"
    fi
}

claude_bots_stop() {
    claude_bots_enabled || return 0
    log_info "Claude adapter remains managed by BAAS until BAAS stops"
}

claude_bots_runtime_path_safe_to_clean() {
    local runtime_path="$1"
    case "$runtime_path" in
        /*) ;;
        *) return 1 ;;
    esac
    case "${runtime_path%/}" in
        ""|/|"${HOME%/}"|"${HOME%/}/.claude"|"${PROJECT_ROOT%/}"|"${SCRIPT_DIR%/}"|"${DEP_DIR%/}")
            return 1
            ;;
    esac
}

claude_bots_clean() {
    [ -f "$CLAUDE_BOTS_STATE_FILE" ] || return 0
    local config_dir workspace runtime_path label
    config_dir="$(jq -r '.claude_config_dir // empty' "$CLAUDE_BOTS_STATE_FILE")"
    workspace="$(jq -r '.workspace // empty' "$CLAUDE_BOTS_STATE_FILE")"
    if { [ -z "$config_dir" ] || [ -z "$workspace" ]; } && claude_profile_enabled; then
        local role name summary port model prompt_file permission
        IFS=$'\x1f' read -r role name summary port config_dir workspace model prompt_file permission < <(claude_profile_entries)
    fi

    for label in config workspace; do
        if [ "$label" = config ]; then runtime_path="$config_dir"; else runtime_path="$workspace"; fi
        [ -n "$runtime_path" ] || continue
        if ! claude_bots_runtime_path_safe_to_clean "$runtime_path"; then
            log_error "Refusing to clean unsafe Claude ${label} path: ${runtime_path}"
            return 1
        fi
        rm -rf "$runtime_path"
        log_info "Cleaned Claude ${label} path: ${runtime_path}"
    done
    rm -f "$CLAUDE_BOTS_STATE_FILE"
}

claude_bots_ready() {
    claude_bots_enabled || return 0
    jq -e '.bots | length == 1' "$CLAUDE_BOTS_STATE_FILE" >/dev/null 2>&1
}

claude_bots_status() {
    claude_bots_enabled || return 0
    if [ ! -f "$CLAUDE_BOTS_STATE_FILE" ]; then echo "  Claude bot: Not ready"; return 0; fi
    jq -r '.bots[] | "  Claude bot (\(.role)): Started (\(.bot_id); relay \(.relay_port); adapter BAAS-managed)"' "$CLAUDE_BOTS_STATE_FILE"
}
