#!/usr/bin/env bash
# scripts/modules/bcs_baas_provider.sh — one BCS Provider for three Claude bots
[[ -n "${_BCS_BAAS_PROVIDER_SH_LOADED:-}" ]] && return 0
_BCS_BAAS_PROVIDER_SH_LOADED=1

BCS_BAAS_PROVIDER_PORT="${BCS_BAAS_PROVIDER_PORT:-28083}"
BCS_BAAS_PROVIDER_LOG="${LOG_DIR}/bcs_baas_provider.log"
BCS_BAAS_PROVIDER_PID_FILE="${DEP_DIR}/bcs_baas_provider.pid"
BCS_BAAS_PROVIDER_STATE_FILE="${DEP_DIR}/bcs_baas_provider.state.json"
BCS_BAAS_PROVIDER_TOKEN_FILE="${DEP_DIR}/bcs_baas_provider.tokens.json"
BCS_BAAS_PROVIDER_BRIDGE="${SCRIPT_DIR}/bcs_baas_provider_bridge.mjs"

bcs_baas_provider_prereqs() {
    claude_bots_enabled || return 0
    check_command python3 || { prereq_error "python3 not found"; return 1; }
    check_command node || { prereq_error "node not found"; return 1; }
    check_command jq || { prereq_error "jq not found"; return 1; }
    [ -f "$BCS_BAAS_PROVIDER_BRIDGE" ] || { prereq_error "bridge missing: ${BCS_BAAS_PROVIDER_BRIDGE}"; return 1; }
}

bcs_baas_provider_runtime_tokens_init() {
    umask 077
    python3 - "$BCS_BAAS_PROVIDER_TOKEN_FILE" <<'PY'
import json
import secrets
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(json.dumps({"baas_token": secrets.token_urlsafe(32), "provider_admin_token": "", "bcs_to_provider_token": "", "provider_bots": {}}), encoding="utf-8")
path.chmod(0o600)
PY
}

bcs_baas_provider_prepare_runtime_tokens() {
    if [ -f "$BCS_BAAS_PROVIDER_TOKEN_FILE" ] && python3 - "$BCS_BAAS_PROVIDER_TOKEN_FILE" <<'PY'
import json
import sys
from pathlib import Path

try:
    value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("baas_token")
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
raise SystemExit(0 if isinstance(value, str) and value else 1)
PY
    then
        return 0
    fi
    bcs_baas_provider_runtime_tokens_init
}

bcs_baas_provider_baas_token() {
    python3 - "$BCS_BAAS_PROVIDER_TOKEN_FILE" <<'PY'
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("baas_token")
if not isinstance(value, str) or not value:
    raise SystemExit("runtime BaaS token is missing")
print(value)
PY
}

# The Backend bot owner and the BCS human who is using the local frontend are
# different identities.  Keep the Backend owner in provider_bot_ref so BaaS
# can resolve its binding, but make the BCS Provider bot belong to the active
# BCS mock user.  Otherwise the Provider bot is healthy yet absent from that
# user's `/bots/my` response and therefore from the frontend's Bot switcher.
bcs_baas_provider_bcs_owner_id() {
    load_frontend_bcs_mock_env
    printf '%s\n' "${BCS_MOCK_USER_ID:-001}"
}

bcs_baas_provider_set_registration_tokens() {
    local provider_admin_token="$1" bcs_to_provider_token="$2"
    BCS_BAAS_PROVIDER_ADMIN_TOKEN="$provider_admin_token" BCS_BAAS_PROVIDER_DOWNLINK_TOKEN="$bcs_to_provider_token" python3 - "$BCS_BAAS_PROVIDER_TOKEN_FILE" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["provider_admin_token"] = os.environ["BCS_BAAS_PROVIDER_ADMIN_TOKEN"]
data["bcs_to_provider_token"] = os.environ["BCS_BAAS_PROVIDER_DOWNLINK_TOKEN"]
path.write_text(json.dumps(data), encoding="utf-8")
path.chmod(0o600)
PY
}

bcs_baas_provider_clear_registration_tokens() {
    python3 - "$BCS_BAAS_PROVIDER_TOKEN_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["provider_admin_token"] = ""
data["bcs_to_provider_token"] = ""
data["provider_bots"] = {}
path.write_text(json.dumps(data), encoding="utf-8")
path.chmod(0o600)
PY
}

bcs_baas_provider_active_display_name() {
    printf '%s（当前）\n' "$1"
}

# Delete only the three Provider bots recorded by this checkout.  BCS has no
# Provider DELETE endpoint, and retaining those public bots after their
# downlink token is discarded leaves visually identical cards that always
# return 401.  Return 2 for a pre-fix runtime file that has no admin token:
# callers can migrate it without pretending the historical bots were removed.
bcs_baas_provider_cleanup_registration() {
    [ -f "$BCS_BAAS_PROVIDER_STATE_FILE" ] || return 0
    if [ ! -f "$BCS_BAAS_PROVIDER_TOKEN_FILE" ]; then
        log_warn "Claude Provider registration state has no runtime credential; preserving it for migration"
        return 2
    fi

    local provider_id provider_admin_token provider_bot_ref encoded_provider_id encoded_provider_ref
    provider_id="$(jq -r '.provider_id // empty' "$BCS_BAAS_PROVIDER_STATE_FILE")"
    provider_admin_token="$(jq -r '.provider_admin_token // empty' "$BCS_BAAS_PROVIDER_TOKEN_FILE")"
    if [ -z "$provider_id" ] || [ -z "$provider_admin_token" ]; then
        log_warn "Claude Provider registration predates lifecycle cleanup; preserving historical BCS cards for migration"
        return 2
    fi
    encoded_provider_id="$(jq -nr --arg value "$provider_id" '$value | @uri')"
    while IFS= read -r provider_bot_ref; do
        encoded_provider_ref="$(jq -nr --arg value "$provider_bot_ref" '$value | @uri')"
        if ! curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS -X DELETE \
            "http://127.0.0.1:${BCS_PORT}/providers/${encoded_provider_id}/bots/${encoded_provider_ref}" \
            -H "Authorization: Bearer ${provider_admin_token}" >/dev/null 2>&1; then
            log_error "Failed to remove a current Claude Provider bot; preserving runtime state to prevent duplicate registration"
            return 1
        fi
    done < <(jq -r '.bots[]?.provider_bot_ref // empty' "$BCS_BAAS_PROVIDER_STATE_FILE")

    bcs_baas_provider_clear_registration_tokens
    rm -f "$BCS_BAAS_PROVIDER_STATE_FILE"
    log_info "Removed the three current Claude Provider bots before clearing their downlink registration"
}

bcs_baas_provider_add_provider_bot_ref() {
    local role="$1" provider_bot_ref="$2"
    BCS_BAAS_PROVIDER_ROLE="$role" BCS_BAAS_PROVIDER_REF="$provider_bot_ref" python3 - "$BCS_BAAS_PROVIDER_TOKEN_FILE" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data.setdefault("provider_bots", {})[os.environ["BCS_BAAS_PROVIDER_ROLE"]] = {
    "provider_bot_ref": os.environ["BCS_BAAS_PROVIDER_REF"],
}
path.write_text(json.dumps(data), encoding="utf-8")
path.chmod(0o600)
PY
}

bcs_baas_provider_wait_ready() {
    local attempts=0
    while [ "$attempts" -lt 30 ]; do
        if bcs_baas_provider_healthy; then
            return 0
        fi
        sleep 0.5
        attempts=$((attempts + 1))
    done
    return 1
}

bcs_baas_provider_healthy() {
    node - "$BCS_BAAS_PROVIDER_PORT" <<'NODE' >/dev/null 2>&1
const http2 = require('node:http2');
const port = process.argv[2];
const client = http2.connect(`http://127.0.0.1:${port}`);
let body = '';
let finished = false;
const finish = (code) => {
  if (finished) return;
  finished = true;
  client.close();
  process.exit(code);
};
const timer = setTimeout(() => finish(1), 2_000);
const request = client.request({ ':method': 'GET', ':path': '/health' });
request.setEncoding('utf8');
request.on('response', (headers) => {
  if (headers[':status'] !== 200) finish(1);
});
request.on('data', (chunk) => { body += chunk; });
request.on('end', () => {
  clearTimeout(timer);
  try {
    finish(JSON.parse(body).ok === true ? 0 : 1);
  } catch {
    finish(1);
  }
});
request.on('error', () => { clearTimeout(timer); finish(1); });
client.on('error', () => { clearTimeout(timer); finish(1); });
request.end();
NODE
}

bcs_baas_provider_register() {
    local backend_entity_id bcs_owner_id provider_payload registration provider_id provider_admin_token bcs_to_provider_token
    backend_entity_id="$(jq -r '.entity_id' "$CLAUDE_BOTS_STATE_FILE")"
    bcs_owner_id="$(bcs_baas_provider_bcs_owner_id)"
    provider_payload="$(jq -n --arg name 'singlebox-claude-code' --arg webhook "http://127.0.0.1:${BCS_BAAS_PROVIDER_PORT}/webhook" '{name: $name, webhook_url: $webhook, auth: {mode: "static_bearer"}, protocol_version: "2.0"}')"
    registration="$(curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS -X POST "http://127.0.0.1:${BCS_PORT}/providers" -H "X-Mock-User-Id: ${bcs_owner_id}" -H 'Content-Type: application/json' -d "$provider_payload")" || return 1
    provider_id="$(jq -r '.provider_id // empty' <<< "$registration")"
    provider_admin_token="$(jq -r '.provider_admin_token // empty' <<< "$registration")"
    bcs_to_provider_token="$(jq -r '.bcs_to_provider_token // empty' <<< "$registration")"
    if [ -z "$provider_id" ] || [ -z "$provider_admin_token" ] || [ -z "$bcs_to_provider_token" ]; then
        log_error "BCS Provider registration returned an incomplete response"
        return 1
    fi
    bcs_baas_provider_set_registration_tokens "$provider_admin_token" "$bcs_to_provider_token"

    local role bot_id name display_name provider_ref bot_payload bot_response runtime_token bot_uuid visibility_response state_bots='[]'
    while IFS=$'\t' read -r role bot_id name; do
        provider_ref="${bot_id}:${backend_entity_id}"
        display_name="$(bcs_baas_provider_active_display_name "$name")"
        bot_payload="$(jq -n --arg name "$display_name" --arg ref "$provider_ref" --arg owner "$bcs_owner_id" --arg role "$role" '{name: $name, provider_bot_ref: $ref, owners: [$owner], summary: ("Local Claude Code " + $role + " bot (current)"), domains: ["claude_code", "local"], skills: ["chat", "collaboration"], scopes: ["local"]}')"
        bot_response="$(curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS -X POST "http://127.0.0.1:${BCS_PORT}/providers/${provider_id}/bots" -H "Authorization: Bearer ${provider_admin_token}" -H 'Content-Type: application/json' -d "$bot_payload")" || return 1
        runtime_token="$(jq -r '.bot_runtime_token // empty' <<< "$bot_response")"
        bot_uuid="$(jq -r '.bot_uuid // empty' <<< "$bot_response")"
        if [ -z "$runtime_token" ] || [ -z "$bot_uuid" ]; then
            log_error "BCS Provider bot registration failed for Claude ${role}"
            return 1
        fi
        # Provider registration is deliberately protected by default.  A
        # mixed local stack promises these three bots can be selected by the
        # five OpenClaw bots, so publish the just-created local Provider bot
        # using its one-time runtime token.  The token stays in the runtime
        # file and is never logged.
        visibility_response="$(curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS -X PUT "http://127.0.0.1:${BCS_PORT}/bots/${bot_uuid}/visibility" -H "Authorization: Bearer ${runtime_token}" -H 'Content-Type: application/json' -d '{"visibility":"public"}')" || return 1
        if ! jq -e '.success == true and .data.visibility == "public"' <<< "$visibility_response" >/dev/null 2>&1; then
            log_error "BCS Provider bot was not made discoverable for Claude ${role}"
            return 1
        fi
        bcs_baas_provider_add_provider_bot_ref "$role" "$provider_ref"
        state_bots="$(jq -c --arg role "$role" --arg ref "$provider_ref" --arg uuid "$bot_uuid" '. + [{role: $role, provider_bot_ref: $ref, bot_uuid: $uuid}]' <<< "$state_bots")"
    done < <(jq -r '.bots[] | [.role, .bot_id, .name] | @tsv' "$CLAUDE_BOTS_STATE_FILE")
    unset provider_admin_token bcs_to_provider_token runtime_token visibility_response
    umask 077
    jq -n --arg provider_id "$provider_id" --arg bcs_owner_id "$bcs_owner_id" --argjson bots "$state_bots" '{provider_id: $provider_id, bcs_owner_id: $bcs_owner_id, bots: $bots}' > "$BCS_BAAS_PROVIDER_STATE_FILE"
    chmod 600 "$BCS_BAAS_PROVIDER_STATE_FILE"
    log_info "Registered one local BCS Provider and three discoverable Claude Provider bots for the active BCS user"
}

bcs_baas_provider_start() {
    claude_bots_enabled || return 0
    bcs_baas_provider_prereqs || return 1
    bcs_ready || { log_error "BCS is not ready; cannot register Provider"; return 1; }
    baas_ready || { log_error "BAAS is not ready; cannot start Provider bridge"; return 1; }
    [ -f "$CLAUDE_BOTS_STATE_FILE" ] || { log_error "Claude bot state is missing"; return 1; }
    mkdir -p "$LOG_DIR"
    stop_port_processes_if_owned "$BCS_BAAS_PROVIDER_PORT" "$PROJECT_ROOT" "BCS BaaS Provider bridge" || true
    require_port_available_after_owned_stop "$BCS_BAAS_PROVIDER_PORT" "BCS BaaS Provider bridge" || return 1
    if [ -f "$BCS_BAAS_PROVIDER_STATE_FILE" ]; then
        local cleanup_rc=0
        bcs_baas_provider_cleanup_registration || cleanup_rc=$?
        if [ "$cleanup_rc" -eq 2 ]; then
            # The prior implementation discarded the Provider admin token.
            # It cannot safely delete those historical BCS records, but a
            # marked current trio prevents users from selecting them again.
            bcs_baas_provider_clear_registration_tokens || return 1
            rm -f "$BCS_BAAS_PROVIDER_STATE_FILE"
            log_warn "Migrating legacy Claude Provider registration; old BCS bot cards remain unmodified"
        elif [ "$cleanup_rc" -ne 0 ]; then
            log_error "Refusing to register duplicate Claude Provider bots while prior cleanup is incomplete"
            return 1
        fi
    fi
    bcs_baas_provider_prepare_runtime_tokens || return 1
    log_info "Starting BCS to BAAS Provider bridge on port ${BCS_BAAS_PROVIDER_PORT}"
    (
        cd "$PROJECT_ROOT"
        nohup node "$BCS_BAAS_PROVIDER_BRIDGE" --port "$BCS_BAAS_PROVIDER_PORT" --token-file "$BCS_BAAS_PROVIDER_TOKEN_FILE" >> "$BCS_BAAS_PROVIDER_LOG" 2>&1 &
        echo $! > "$BCS_BAAS_PROVIDER_PID_FILE"
    )
    bcs_baas_provider_wait_ready || { log_error "Provider bridge did not become ready"; return 1; }
    bcs_baas_provider_register || {
        log_error "Provider registration failed; check ${BCS_BAAS_PROVIDER_LOG}"
        bcs_baas_provider_stop || true
        return 1
    }
}

bcs_baas_provider_stop() {
    claude_bots_enabled || return 0
    local pid
    if [ -f "$BCS_BAAS_PROVIDER_PID_FILE" ]; then
        pid="$(cat "$BCS_BAAS_PROVIDER_PID_FILE" 2>/dev/null || true)"
        if ! stop_process_if_owned "$pid" "$PROJECT_ROOT" "BCS BaaS Provider bridge" && kill -0 "$pid" 2>/dev/null; then
            log_error "Refusing to remove Provider bridge state while its PID is not owned by this checkout"
            return 1
        fi
    fi
    stop_port_processes_if_owned "$BCS_BAAS_PROVIDER_PORT" "$PROJECT_ROOT" "BCS BaaS Provider bridge" || true
    if port_is_listening "$BCS_BAAS_PROVIDER_PORT"; then
        log_error "Refusing to remove Provider bridge state while port ${BCS_BAAS_PROVIDER_PORT} remains in use"
        return 1
    fi
    rm -f "$BCS_BAAS_PROVIDER_PID_FILE"
    local cleanup_rc=0
    bcs_baas_provider_cleanup_registration || cleanup_rc=$?
    if [ "$cleanup_rc" -eq 1 ]; then
        return 1
    fi
    if [ "$cleanup_rc" -eq 2 ]; then
        log_warn "Claude Provider cleanup requires migration on its next start"
    fi
}

bcs_baas_provider_ready() {
    claude_bots_enabled || return 0
    bcs_baas_provider_wait_ready && [ -f "$BCS_BAAS_PROVIDER_STATE_FILE" ]
}

bcs_baas_provider_status() {
    claude_bots_enabled || return 0
    if bcs_baas_provider_healthy && [ -f "$BCS_BAAS_PROVIDER_STATE_FILE" ]; then
        echo "  Claude Provider: Running (bridge ${BCS_BAAS_PROVIDER_PORT}; 3 Provider bots)"
    else
        echo "  Claude Provider: Stopped"
    fi
}

bcs_baas_provider_help() {
    echo "bcs_baas_provider - local BCS Provider bridge for three Claude bots (mixed mode only)"
}
