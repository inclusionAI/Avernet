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

bcs_baas_provider_bot_ref() {
    local role="$1" bot_id="$2" entity_id="$3"
    case "$role" in
        platform-data) printf 'merchant-platform-data:%s\n' "$entity_id" ;;
        *) printf '%s:%s\n' "$bot_id" "$entity_id" ;;
    esac
}

bcs_baas_provider_expected_bot_uuid() {
    local role="$1" name="$2"
    case "$role" in
        platform-data) printf '%s\n' "$name" ;;
        *) return 1 ;;
    esac
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

bcs_baas_provider_state_matches_expected_identity() {
    [ -f "$BCS_BAAS_PROVIDER_STATE_FILE" ] || return 1
    [ -f "$CLAUDE_BOTS_STATE_FILE" ] || return 1
    local entity_id owner role bot_id name provider_bot_ref bot_uuid expected_bots='[]'
    entity_id="$(jq -r '.entity_id // empty' "$CLAUDE_BOTS_STATE_FILE")"
    owner="$(bcs_baas_provider_bcs_owner_id)"
    [ -n "$entity_id" ] && [ -n "$owner" ] || return 1
    while IFS=$'\t' read -r role bot_id name; do
        provider_bot_ref="$(bcs_baas_provider_bot_ref "$role" "$bot_id" "$entity_id")"
        bot_uuid="$(bcs_baas_provider_expected_bot_uuid "$role" "$name")" || return 1
        expected_bots="$(jq -c --arg role "$role" --arg ref "$provider_bot_ref" --arg uuid "$bot_uuid" \
            '. + [{role: $role, provider_bot_ref: $ref, bot_uuid: $uuid}]' <<< "$expected_bots")" || return 1
    done < <(jq -r '.bots[] | [.role, .bot_id, .name] | @tsv' "$CLAUDE_BOTS_STATE_FILE")
    jq -e --arg owner "$owner" --argjson expected_bots "$expected_bots" '
        (.provider_id | type == "string" and length > 0)
        and .bcs_owner_id == $owner
        and .bots == $expected_bots
    ' "$BCS_BAAS_PROVIDER_STATE_FILE" >/dev/null
}

bcs_baas_provider_registration_is_reusable() {
    bcs_baas_provider_state_matches_expected_identity || return 1
    [ -f "$BCS_BAAS_PROVIDER_TOKEN_FILE" ] || return 1
    local provider_id provider_admin_token provider_response bindings_response role provider_bot_ref bot_uuid runtime_token
    provider_id="$(jq -r '.provider_id // empty' "$BCS_BAAS_PROVIDER_STATE_FILE")"
    provider_admin_token="$(jq -r '.provider_admin_token // empty' "$BCS_BAAS_PROVIDER_TOKEN_FILE")"
    [ -n "$provider_id" ] && [ -n "$provider_admin_token" ] || return 1
    provider_response="$(curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS \
        "http://127.0.0.1:${BCS_PORT}/providers/${provider_id}" \
        -H "Authorization: Bearer ${provider_admin_token}")" || return 1
    jq -e --arg provider_id "$provider_id" '
        .provider_id == $provider_id and .name == "singlebox-merchant-claude" and .disabled == false
    ' <<< "$provider_response" >/dev/null || return 1
    bindings_response="$(curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS \
        "http://127.0.0.1:${BCS_PORT}/providers/${provider_id}/bots" \
        -H "Authorization: Bearer ${provider_admin_token}")" || return 1
    while IFS=$'\t' read -r role provider_bot_ref bot_uuid; do
        runtime_token="$(jq -r --arg role "$role" '.provider_bots[$role].bot_runtime_token // empty' "$BCS_BAAS_PROVIDER_TOKEN_FILE")"
        [ -n "$runtime_token" ] || return 1
        jq -e --arg ref "$provider_bot_ref" --arg uuid "$bot_uuid" '
            .items | any(.provider_bot_ref == $ref and .bot_uuid == $uuid and .disabled == false)
        ' <<< "$bindings_response" >/dev/null || return 1
    done < <(jq -r '.bots[] | [.role, .provider_bot_ref, .bot_uuid] | @tsv' "$BCS_BAAS_PROVIDER_STATE_FILE")
}

bcs_baas_provider_retire_legacy_registration() {
    [ -f "$BCS_BAAS_PROVIDER_STATE_FILE" ] || return 0
    [ -f "$BCS_BAAS_PROVIDER_TOKEN_FILE" ] || { bcs_baas_provider_clear_registration; rm -f "$BCS_BAAS_PROVIDER_STATE_FILE"; return 0; }
    local provider_id provider_admin_token provider_bot_ref encoded_id encoded_ref delete_failed=0
    provider_id="$(jq -r '.provider_id // empty' "$BCS_BAAS_PROVIDER_STATE_FILE")"
    provider_admin_token="$(jq -r '.provider_admin_token // empty' "$BCS_BAAS_PROVIDER_TOKEN_FILE")"
    if [ -n "$provider_id" ] && [ -n "$provider_admin_token" ]; then
        encoded_id="$(jq -nr --arg value "$provider_id" '$value | @uri')"
        while IFS= read -r provider_bot_ref; do
            encoded_ref="$(jq -nr --arg value "$provider_bot_ref" '$value | @uri')"
            curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS -X DELETE \
                "http://127.0.0.1:${BCS_PORT}/providers/${encoded_id}/bots/${encoded_ref}" \
                -H "Authorization: Bearer ${provider_admin_token}" >/dev/null || delete_failed=1
        done < <(jq -r '.bots[]?.provider_bot_ref // empty' "$BCS_BAAS_PROVIDER_STATE_FILE")
    else
        delete_failed=1
    fi
    bcs_baas_provider_clear_registration || return 1
    rm -f "$BCS_BAAS_PROVIDER_STATE_FILE"
    if [ "$delete_failed" -eq 0 ]; then
        log_info "Retired legacy merchant Claude Provider registration; re-add 平台数据分析 to existing groups once"
    else
        log_warn "Could not verify legacy merchant Claude Provider removal; cleared local registration state before recovery"
    fi
}

bcs_baas_provider_ensure_registration() {
    if [ ! -f "$BCS_BAAS_PROVIDER_STATE_FILE" ]; then
        bcs_baas_provider_register
        return
    fi
    if bcs_baas_provider_registration_is_reusable; then
        local provider_id bot_uuid
        provider_id="$(jq -r '.provider_id' "$BCS_BAAS_PROVIDER_STATE_FILE")"
        bot_uuid="$(jq -r '.bots[0].bot_uuid' "$BCS_BAAS_PROVIDER_STATE_FILE")"
        log_info "Reusing merchant Claude Provider registration provider_id=${provider_id} bot_uuid=${bot_uuid}"
        return
    fi
    if bcs_baas_provider_state_matches_expected_identity; then
        bcs_baas_provider_clear_registration || return 1
        rm -f "$BCS_BAAS_PROVIDER_STATE_FILE"
        log_warn "Merchant Claude Provider registration is missing from BCS; creating a replacement"
    else
        log_info "Migrating legacy merchant Claude Provider registration to stable Bot ID 平台数据分析"
        bcs_baas_provider_retire_legacy_registration || return 1
    fi
    bcs_baas_provider_register
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

    local role bot_id name provider_ref expected_bot_uuid payload bot_response runtime_token bot_uuid visibility state_bots='[]'
    while IFS=$'\t' read -r role bot_id name; do
        provider_ref="$(bcs_baas_provider_bot_ref "$role" "$bot_id" "$entity_id")"
        expected_bot_uuid="$(bcs_baas_provider_expected_bot_uuid "$role" "$name")" || { log_error "No stable BCS Bot ID is configured for Claude role=${role}"; return 1; }
        payload="$(jq -n --arg name "$name" --arg ref "$provider_ref" --arg owner "$owner" '{name: $name, provider_bot_ref: $ref, owners: [$owner], summary: "Local Claude Code platform data bot", domains: ["claude_code", "local-commerce"], skills: ["chat", "data-analysis"], scopes: ["local"]}')"
        bot_response="$(curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS -X POST "http://127.0.0.1:${BCS_PORT}/providers/${provider_id}/bots" -H "Authorization: Bearer ${provider_admin_token}" -H 'Content-Type: application/json' -d "$payload")" || return 1
        runtime_token="$(jq -r '.bot_runtime_token // empty' <<< "$bot_response")"
        bot_uuid="$(jq -r '.bot_uuid // empty' <<< "$bot_response")"
        [ -n "$runtime_token" ] && [ -n "$bot_uuid" ] || { log_error "BCS Provider bot registration failed for ${role}"; return 1; }
        [ "$bot_uuid" = "$expected_bot_uuid" ] || { log_error "BCS Provider bot ID mismatch for ${role}; expected=${expected_bot_uuid} got=${bot_uuid}"; return 1; }
        visibility="$(curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS -X PUT "http://127.0.0.1:${BCS_PORT}/bots/${bot_uuid}/visibility" -H "Authorization: Bearer ${runtime_token}" -H 'Content-Type: application/json' -d '{"visibility":"public"}')" || return 1
        jq -e '.success == true and .data.visibility == "public"' <<< "$visibility" >/dev/null || { log_error "BCS Provider bot is not discoverable"; return 1; }
        bcs_baas_provider_add_bot_token "$role" "$provider_ref" "$runtime_token" || return 1
        state_bots="$(jq -c --arg role "$role" --arg ref "$provider_ref" --arg uuid "$bot_uuid" '. + [{role: $role, provider_bot_ref: $ref, bot_uuid: $uuid}]' <<< "$state_bots")"
        log_info "Registered local Claude Provider bot role=${role} bot_uuid=${bot_uuid} visibility=public"
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
    bcs_baas_provider_ensure_registration || { bcs_baas_provider_stop || true; return 1; }
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
    log_info "Stopped BCS to BAAS Provider bridge; preserving merchant Claude Provider registration"
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
