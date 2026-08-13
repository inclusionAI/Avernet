#!/usr/bin/env bash
# scripts/modules/bcs_baas_provider.sh — local BCS Provider for merchant Claude bot
[[ -n "${_BCS_BAAS_PROVIDER_SH_LOADED:-}" ]] && return 0
_BCS_BAAS_PROVIDER_SH_LOADED=1

BCS_BAAS_PROVIDER_PORT="${BCS_BAAS_PROVIDER_PORT:-28083}"
BCS_BAAS_PROVIDER_LOG="${LOG_DIR}/bcs_baas_provider.merchant_hybrid.log"
BCS_BAAS_PROVIDER_PID_FILE="${DEP_DIR}/bcs_baas_provider.merchant_hybrid.pid"
BCS_BAAS_PROVIDER_STATE_FILE="${DEP_DIR}/bcs_baas_provider.merchant_hybrid.state.json"
BCS_BAAS_PROVIDER_TOKEN_FILE="${DEP_DIR}/bcs_baas_provider.merchant_hybrid.tokens.json"
BCS_BAAS_PROVIDER_BRIDGE="${SCRIPT_DIR}/bcs_baas_provider_bridge.mjs"

bcs_baas_provider_enabled() {
    claude_bots_enabled
}

bcs_baas_provider_prereqs() {
    bcs_baas_provider_enabled || return 0
    check_command python3 || { prereq_error "python3 not found"; return 1; }
    check_command node || { prereq_error "node not found"; return 1; }
    check_command jq || { prereq_error "jq not found"; return 1; }
    [ -f "$BCS_BAAS_PROVIDER_BRIDGE" ] || { prereq_error "bridge missing: ${BCS_BAAS_PROVIDER_BRIDGE}"; return 1; }
}

bcs_baas_provider_prepare_runtime_tokens() {
    if [ -f "$BCS_BAAS_PROVIDER_TOKEN_FILE" ] && jq -e '(.baas_token | type == "string" and length > 0)' "$BCS_BAAS_PROVIDER_TOKEN_FILE" >/dev/null 2>&1; then
        return 0
    fi
    umask 077
    python3 - "$BCS_BAAS_PROVIDER_TOKEN_FILE" <<'PY'
import json
import secrets
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(json.dumps({
    "baas_token": secrets.token_urlsafe(32),
    "provider_id": "",
    "provider_admin_token": "",
    "bcs_to_provider_token": "",
    "provider_bots": {},
}), encoding="utf-8")
path.chmod(0o600)
PY
}

bcs_baas_provider_baas_token() {
    jq -r '.baas_token // empty' "$BCS_BAAS_PROVIDER_TOKEN_FILE"
}

bcs_baas_provider_bcs_owner_id() {
    load_frontend_bcs_mock_env
    printf '%s\n' "${BCS_MOCK_USER_ID:-001}"
}

bcs_baas_provider_update_tokens() {
    local provider_id="$1" provider_admin_token="$2" bcs_to_provider_token="$3"
    jq --arg provider_id "$provider_id" --arg provider_admin_token "$provider_admin_token" --arg bcs_to_provider_token "$bcs_to_provider_token" \
        '.provider_id = $provider_id | .provider_admin_token = $provider_admin_token | .bcs_to_provider_token = $bcs_to_provider_token' \
        "$BCS_BAAS_PROVIDER_TOKEN_FILE" > "${BCS_BAAS_PROVIDER_TOKEN_FILE}.tmp" || return 1
    mv "${BCS_BAAS_PROVIDER_TOKEN_FILE}.tmp" "$BCS_BAAS_PROVIDER_TOKEN_FILE"
    chmod 600 "$BCS_BAAS_PROVIDER_TOKEN_FILE"
}

bcs_baas_provider_add_bot_token() {
    local role="$1" provider_bot_ref="$2" bot_runtime_token="$3"
    jq --arg role "$role" --arg ref "$provider_bot_ref" --arg token "$bot_runtime_token" \
        '.provider_bots[$role] = {provider_bot_ref: $ref, bot_runtime_token: $token}' \
        "$BCS_BAAS_PROVIDER_TOKEN_FILE" > "${BCS_BAAS_PROVIDER_TOKEN_FILE}.tmp" || return 1
    mv "${BCS_BAAS_PROVIDER_TOKEN_FILE}.tmp" "$BCS_BAAS_PROVIDER_TOKEN_FILE"
    chmod 600 "$BCS_BAAS_PROVIDER_TOKEN_FILE"
}

bcs_baas_provider_clear_registration() {
    [ -f "$BCS_BAAS_PROVIDER_TOKEN_FILE" ] || return 0
    jq '.provider_id = "" | .provider_admin_token = "" | .bcs_to_provider_token = "" | .provider_bots = {}' \
        "$BCS_BAAS_PROVIDER_TOKEN_FILE" > "${BCS_BAAS_PROVIDER_TOKEN_FILE}.tmp" || return 1
    mv "${BCS_BAAS_PROVIDER_TOKEN_FILE}.tmp" "$BCS_BAAS_PROVIDER_TOKEN_FILE"
    chmod 600 "$BCS_BAAS_PROVIDER_TOKEN_FILE"
}

bcs_baas_provider_healthy() {
    node - "$BCS_BAAS_PROVIDER_PORT" <<'NODE' >/dev/null 2>&1
const http2 = require('node:http2');
const client = http2.connect(`http://127.0.0.1:${process.argv[2]}`);
let body = '';
let done = false;
const finish = code => { if (!done) { done = true; client.close(); process.exit(code); } };
const timer = setTimeout(() => finish(1), 2000);
const request = client.request({ ':method': 'GET', ':path': '/health' });
request.setEncoding('utf8');
request.on('response', headers => { if (headers[':status'] !== 200) finish(1); });
request.on('data', chunk => { body += chunk; });
request.on('end', () => { clearTimeout(timer); try { finish(JSON.parse(body).ok === true ? 0 : 1); } catch { finish(1); } });
request.on('error', () => { clearTimeout(timer); finish(1); });
client.on('error', () => { clearTimeout(timer); finish(1); });
request.end();
NODE
}

bcs_baas_provider_wait_ready() {
    local attempt=0
    while [ "$attempt" -lt 30 ]; do
        bcs_baas_provider_healthy && return 0
        sleep 0.5
        attempt=$((attempt + 1))
    done
    return 1
}

bcs_baas_provider_cleanup_registration() {
    [ -f "$BCS_BAAS_PROVIDER_STATE_FILE" ] || return 0
    [ -f "$BCS_BAAS_PROVIDER_TOKEN_FILE" ] || return 1
    local provider_id provider_admin_token provider_bot_ref encoded_id encoded_ref
    provider_id="$(jq -r '.provider_id // empty' "$BCS_BAAS_PROVIDER_STATE_FILE")"
    provider_admin_token="$(jq -r '.provider_admin_token // empty' "$BCS_BAAS_PROVIDER_TOKEN_FILE")"
    if [ -z "$provider_id" ] || [ -z "$provider_admin_token" ]; then
        log_warn "Cannot remove stale merchant Claude Provider registration without its runtime credential"
        return 1
    fi
    encoded_id="$(jq -nr --arg value "$provider_id" '$value | @uri')"
    while IFS= read -r provider_bot_ref; do
        encoded_ref="$(jq -nr --arg value "$provider_bot_ref" '$value | @uri')"
        curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS -X DELETE \
            "http://127.0.0.1:${BCS_PORT}/providers/${encoded_id}/bots/${encoded_ref}" \
            -H "Authorization: Bearer ${provider_admin_token}" >/dev/null || return 1
    done < <(jq -r '.bots[]?.provider_bot_ref // empty' "$BCS_BAAS_PROVIDER_STATE_FILE")
    bcs_baas_provider_clear_registration || return 1
    rm -f "$BCS_BAAS_PROVIDER_STATE_FILE"
    log_info "Removed the current merchant Claude Provider registration"
}

bcs_baas_provider_register() {
    local entity_id owner provider_payload response provider_id provider_admin_token bcs_token
    entity_id="$(jq -r '.entity_id' "$CLAUDE_BOTS_STATE_FILE")"
    owner="$(bcs_baas_provider_bcs_owner_id)"
    provider_payload="$(jq -n --arg webhook "http://127.0.0.1:${BCS_BAAS_PROVIDER_PORT}/webhook" '{name: "singlebox-merchant-claude", webhook_url: $webhook, auth: {mode: "static_bearer"}, protocol_version: "2.0"}')"
    response="$(curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS -X POST "http://127.0.0.1:${BCS_PORT}/providers" -H "X-Mock-User-Id: ${owner}" -H 'Content-Type: application/json' -d "$provider_payload")" || return 1
    provider_id="$(jq -r '.provider_id // empty' <<< "$response")"
    provider_admin_token="$(jq -r '.provider_admin_token // empty' <<< "$response")"
    bcs_token="$(jq -r '.bcs_to_provider_token // empty' <<< "$response")"
    [ -n "$provider_id" ] && [ -n "$provider_admin_token" ] && [ -n "$bcs_token" ] || { log_error "BCS Provider registration returned incomplete metadata"; return 1; }
    bcs_baas_provider_update_tokens "$provider_id" "$provider_admin_token" "$bcs_token" || return 1

    local role bot_id name provider_ref payload bot_response runtime_token bot_uuid visibility state_bots='[]'
    while IFS=$'\t' read -r role bot_id name; do
        provider_ref="${bot_id}:${entity_id}"
        payload="$(jq -n --arg name "${name}（当前）" --arg ref "$provider_ref" --arg owner "$owner" '{name: $name, provider_bot_ref: $ref, owners: [$owner], summary: "Local Claude Code platform data bot", domains: ["claude_code", "local-commerce"], skills: ["chat", "data-analysis"], scopes: ["local"]}')"
        bot_response="$(curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS -X POST "http://127.0.0.1:${BCS_PORT}/providers/${provider_id}/bots" -H "Authorization: Bearer ${provider_admin_token}" -H 'Content-Type: application/json' -d "$payload")" || return 1
        runtime_token="$(jq -r '.bot_runtime_token // empty' <<< "$bot_response")"
        bot_uuid="$(jq -r '.bot_uuid // empty' <<< "$bot_response")"
        [ -n "$runtime_token" ] && [ -n "$bot_uuid" ] || { log_error "BCS Provider bot registration failed for ${role}"; return 1; }
        visibility="$(curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS -X PUT "http://127.0.0.1:${BCS_PORT}/bots/${bot_uuid}/visibility" -H "Authorization: Bearer ${runtime_token}" -H 'Content-Type: application/json' -d '{"visibility":"public"}')" || return 1
        jq -e '.success == true and .data.visibility == "public"' <<< "$visibility" >/dev/null || { log_error "BCS Provider bot is not discoverable"; return 1; }
        bcs_baas_provider_add_bot_token "$role" "$provider_ref" "$runtime_token" || return 1
        state_bots="$(jq -c --arg role "$role" --arg ref "$provider_ref" --arg uuid "$bot_uuid" '. + [{role: $role, provider_bot_ref: $ref, bot_uuid: $uuid}]' <<< "$state_bots")"
    done < <(jq -r '.bots[] | [.role, .bot_id, .name] | @tsv' "$CLAUDE_BOTS_STATE_FILE")
    umask 077
    jq -n --arg provider_id "$provider_id" --arg owner "$owner" --argjson bots "$state_bots" '{provider_id: $provider_id, bcs_owner_id: $owner, bots: $bots}' > "$BCS_BAAS_PROVIDER_STATE_FILE"
    chmod 600 "$BCS_BAAS_PROVIDER_STATE_FILE"
    log_info "Registered one local BCS Provider and one merchant Claude Provider bot"
}

bcs_baas_provider_start() {
    bcs_baas_provider_enabled || return 0
    bcs_baas_provider_prereqs || return 1
    bcs_ready || { log_error "BCS is not ready; cannot register Provider"; return 1; }
    baas_ready || { log_error "BAAS is not ready; cannot start Provider bridge"; return 1; }
    [ -f "$CLAUDE_BOTS_STATE_FILE" ] || { log_error "Claude bot state is missing"; return 1; }
    bcs_baas_provider_prepare_runtime_tokens || return 1
    if [ -f "$BCS_BAAS_PROVIDER_STATE_FILE" ]; then
        bcs_baas_provider_cleanup_registration || { log_error "Refusing duplicate Provider registration while cleanup is incomplete"; return 1; }
    fi
    mkdir -p "$LOG_DIR"
    stop_port_processes_if_owned "$BCS_BAAS_PROVIDER_PORT" "$PROJECT_ROOT" "BCS BaaS Provider bridge" || true
    require_port_available_after_owned_stop "$BCS_BAAS_PROVIDER_PORT" "BCS BaaS Provider bridge" || return 1
    log_info "Starting BCS to BAAS Provider bridge on port ${BCS_BAAS_PROVIDER_PORT}"
    (
        cd "$PROJECT_ROOT"
        nohup perl -MPOSIX=setsid -e 'setsid() or die "setsid failed: $!\\n"; exec @ARGV' node "$BCS_BAAS_PROVIDER_BRIDGE" \
            --port "$BCS_BAAS_PROVIDER_PORT" --token-file "$BCS_BAAS_PROVIDER_TOKEN_FILE" >> "$BCS_BAAS_PROVIDER_LOG" 2>&1 &
        echo $! > "$BCS_BAAS_PROVIDER_PID_FILE"
    )
    bcs_baas_provider_wait_ready || { log_error "Provider bridge did not become ready"; return 1; }
    bcs_baas_provider_register || { bcs_baas_provider_stop || true; return 1; }
}

bcs_baas_provider_stop() {
    bcs_baas_provider_enabled || return 0
    local pid
    if [ -f "$BCS_BAAS_PROVIDER_PID_FILE" ]; then
        pid="$(cat "$BCS_BAAS_PROVIDER_PID_FILE" 2>/dev/null || true)"
        stop_process_if_owned "$pid" "$PROJECT_ROOT" "BCS BaaS Provider bridge" || return 1
    fi
    stop_port_processes_if_owned "$BCS_BAAS_PROVIDER_PORT" "$PROJECT_ROOT" "BCS BaaS Provider bridge" || true
    rm -f "$BCS_BAAS_PROVIDER_PID_FILE"
    if bcs_ready; then bcs_baas_provider_cleanup_registration || return 1; fi
}

bcs_baas_provider_ready() {
    bcs_baas_provider_enabled || return 0
    bcs_baas_provider_healthy && [ -f "$BCS_BAAS_PROVIDER_STATE_FILE" ]
}

bcs_baas_provider_status() {
    bcs_baas_provider_enabled || return 0
    if bcs_baas_provider_healthy && [ -f "$BCS_BAAS_PROVIDER_STATE_FILE" ]; then
        echo "  Claude Provider: Running (bridge ${BCS_BAAS_PROVIDER_PORT}; one Provider bot)"
    else
        echo "  Claude Provider: Stopped"
    fi
}
