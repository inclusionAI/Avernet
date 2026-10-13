# ============================================================================
# Trusted Registration Seeding (shared helper)
# ============================================================================
# URL-encode a query-parameter value (register tokens contain +/=).
_url_encode_param() {
    command -v python3 >/dev/null 2>&1 \
        && python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1" \
        || jq -rn --arg v "$1" '$v|@uri'
}
_session_has_local_token() {
    local session_file="$1"
    [ -s "$session_file" ] || return 1
    jq -e --arg bcs_url "$BCS_URL" '
        (.token | type) == "string"
        and (.token | length) > 0
        and ((.bot_uuid // "") | type == "string")
        and .bcs_url == $bcs_url
    ' "$session_file" >/dev/null 2>&1
}
# Seed every local bot's human ownership through the TRUSTED register
# surface (bot-authority ruling T6d-1): the raw WS connect + onboard
# flow never mints authority, so story humans (X-Mock-User-Id) would
# get 403 "User ... does not control bot ..." on every Human->Bot
# operation. For each bot with no usable session token yet:
#   1. GET /register/token under the mock human identity
#   2. POST /register?token=...&bot-name=<BOT*_ID>
#      -> commits owner + version 1 + default profile atomically
#   3. write .bcs/session.json (bot_uuid/token/bcs_url) so the BCN
#      plugin RESUMES the registered bot with its returned token
# Bots that already carry a session token for this BCS URL are left
# untouched (idempotent re-runs; identity never rotates implicitly).
# A 401 on the register-token mint means the local mock-auth human
# chain is unavailable (e.g. a dev/real-auth stack that can never
# resolve X-Mock-User-Id): seeding downgrades to a warning there, so
# such stacks keep starting unowned; story-style Human->Bot flows
# will 403 until they adopt mock/local auth. Skip entirely with
# BCS_TRUSTED_SEED_BOTS=0.
seed_trusted_registrations() {
    local base_url="${BCS_API_BASE_URL}"
    local mock_user="${BCS_MOCK_USER_ID:-001}"
    local mock_nick="${BCS_MOCK_USER_NICK_NAME:-admin}"
    local spec bot_name profile session_file
    local token mint register mint_status bot_uuid bot_token enc_token enc_name
    for spec in \
        "$BOT1_ID:$BOT1_PROFILE" \
        "$BOT2_ID:$BOT2_PROFILE" \
        "$BOT3_ID:$BOT3_PROFILE" \
        "$BOT4_ID:$BOT4_PROFILE" \
        "$BOT5_ID:$BOT5_PROFILE"; do
        bot_name="${spec%%:*}"
        profile="${spec#*:}"
        session_file="$(bcs_session_file_for "$profile")"
        if _session_has_local_token "$session_file"; then
            info "Preserving existing BCS session for $bot_name (resume, no re-registration)"
            continue
        fi
        mint="$(curl --noproxy '*' -s -S -w $'\n%{http_code}' \
            -H "X-Mock-User-Id: $mock_user" \
            -H "X-Mock-Nick-Name: $mock_nick" \
            "${base_url}/register/token")" \
            || { fail "register token mint request failed for $bot_name"; return 1; }
        mint_status="${mint##*$'\n'}"
        mint="${mint%$'\n'*}"
        token="$(printf '%s' "$mint" | jq -r 'if (.token|type)=="string" then .token else empty end' 2>/dev/null)"
        if [ "$mint_status" = "401" ] && [ -z "$token" ]; then
            warn "$bot_name left human-unowned: /register/token returned 401 (mock human identity unavailable on this stack)"
            warn "enable the local mock-auth human chain (auth chain 'local' with allow_mock_headers, or BCS_AUTH_MOCK=1); Human->Bot story ops will 403"
            continue
        fi
        if [ -z "$token" ]; then
            fail "register token mint rejected for $bot_name (HTTP $mint_status): $mint"
            fail "set BCS_TRUSTED_SEED_BOTS=0 to skip trusted registration seeding"
            return 1
        fi
        enc_token="$(_url_encode_param "$token")"
        enc_name="$(_url_encode_param "$bot_name")"
        register="$(curl --noproxy '*' -s -S -X POST \
            "${base_url}/register?token=${enc_token}&bot-name=${enc_name}")" \
            || { fail "trusted registration request failed for $bot_name"; return 1; }
        bot_uuid="$(printf '%s' "$register" | jq -r 'if (.bot_uuid|type)=="string" then .bot_uuid else empty end' 2>/dev/null)"
        bot_token="$(printf '%s' "$register" | jq -r 'if (.bot_token|type)=="string" then .bot_token else empty end' 2>/dev/null)"
        if [ -z "$bot_uuid" ] || [ -z "$bot_token" ]; then
            fail "trusted registration failed for $bot_name: $register"
            return 1
        fi
        mkdir -p "$(dirname "$session_file")"
        jq -n \
            --arg bot_uuid "$bot_uuid" \
            --arg token "$bot_token" \
            --arg bcs_url "$BCS_URL" \
            '{bot_uuid: $bot_uuid, token: $token, bcs_url: $bcs_url}' \
            > "$session_file"
        pass "$bot_name registered through the trusted register surface (bot_uuid=$bot_uuid, owner=human_$mock_user)"
    done
}

