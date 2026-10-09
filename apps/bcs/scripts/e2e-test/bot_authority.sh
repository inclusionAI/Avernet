#!/bin/bash
# bot_authority.sh — Bot owner/manager authority E2E coverage tests
#
# Covers the new Human authority surface (bot owner/manager plan
# 2026-10-08, Tasks 9/13/14):
#   * GET /openapi/v1/collaboration/bots/mine — the access_relation labels
#     over the live Gateway-Principal lane (plus label parity on the
#     legacy /bots/my mock-human entrypoint)；
#   * GET/PUT/DELETE /openapi/v1/collaboration/bots/{id}/managers/{user} —
#     the manager list, grant, and source-scoped revoke；
#   * the confirmed ownership-transfer lane: GET ownership, POST create,
#     GET listing/receipt, POST accept — the roles flip and the pending
#     slot releases；
#   * PUT/POST/DELETE /api/v1/bots/{id}/manager-sources/teams/{team} — the
#     credential-gated team-manager platform slice (normal sync + both
#     single-member repairs)， run when the deployment armed the lane；
#   * a maintenance-binary operator probe: `bcs-ownership-migrate` usage
#     semantics (it is a dedicated governed binary — bcs-cli gains NO new
#     leaf for the cutover, and this suite must not pretend otherwise).
#
# Gatekeeping is honest, not fabricated:
#   - v1 (OpenAPI) calls need a Gateway-Principal JWT signed with the
#     server's principal signing key, read from
#     $AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE (the singlebox module
#     exports it). Missing material -> the cases SKIP (77), never pass.
#   - the team slice stays unmounted unless the deployment enabled
#     [team_manager_sync]; a 404 probe result is recorded as SKIP.

E2E_TESTS_BOT_AUTHORITY=(
    "story_bot_authority_mine_labels"
    "story_bot_manager_grant_and_revoke"
    "story_ownership_transfer_confirmed"
    "story_team_manager_sources_platform_sync"
    "story_ownership_migrate_maintenance_binary_probes"
)

# The v1 base the OpenAPI surface mounts at.
_AUTHORITY_V1_BASE="${BCS_API_BASE_URL%/}/openapi/v1/collaboration"

# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

# Mint an HS256 Gateway-Principal JWT for `user_id` using python3's stdlib
# (hmac+base64). Fails (returns 1, printing nothing) when no signing-key
# material is available — callers must treat that as an honest skip.
_authority_principal_token() {
    local user_id="$1"
    python3 - "$user_id" <<'PY'
import base64, hmac, hashlib, json, os, sys, time
user_id = sys.argv[1]
key = os.environ.get("AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE", "").encode()
if not key.strip():
    sys.exit(1)
now = int(time.time())
header = {"alg": "HS256", "typ": "JWT", "kid": "bare"}
claims = {
    "iss": "gateway",
    "aud": "bcs",
    "iat": now - 1,
    "exp": now + 300,
    "principals": [{
        "type": "user",
        "tenant": "tenant-a",
        "subject": {"id": user_id, "username": user_id, "tenant_id": "tenant-a"},
    }],
}
def b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()
signing_input = b64(header) + "." + b64(claims)
sig = base64.urlsafe_b64encode(hmac.new(key, signing_input.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
print(signing_input + "." + sig)
PY
}

# Mint the trusted team-manager service credential (HS256, dedicated
# `team_manager_sync` purpose) from $BCS_TEAM_MANAGER_SYNC_SIGNING_KEY.
_authority_team_credential() {
    python3 <<'PY'
import base64, hmac, hashlib, json, os, sys, time
key = os.environ.get("BCS_TEAM_MANAGER_SYNC_SIGNING_KEY", "").encode()
if not key.strip():
    sys.exit(1)
# Same priority as `bcs_config::resolve_env`, plus the singlebox wrapper's
# own BCS_SERVER_ENV (it passes the value to the server process inline; the
# credential's env claim must match the assembled verifier's env).
env_raw = (
    os.environ.get("SERVER_ENV")
    or os.environ.get("REAL_SERVER_ENV")
    or os.environ.get("ALIPAY_APP_ENV")
    or os.environ.get("BCS_SERVER_ENV")
    or ""
)
env_raw = env_raw.lower()
env = {"prod": "prod", "gray": "gray", "pre": "pre", "prepub": "pre", "local": "local"}.get(env_raw, "dev")
now = int(time.time())
header = {"alg": "HS256", "typ": "JWT"}
claims = {
    "iss": "bcn",
    "aud": "bcn-team-manager-sync",
    "purpose": "team_manager_sync",
    "sub": "e2e-story-platform",
    "env": env,
    "iat": now - 1,
    "exp": now + 600,
}
def b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()
signing_input = b64(header) + "." + b64(claims)
sig = base64.urlsafe_b64encode(hmac.new(key, signing_input.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
print(signing_input + "." + sig)
PY
}

# One request against the mounted v1 surface with a formal Human
# principal. Sets HTTP_STATUS / RESPONSE like _api_request.
_authority_v1_request() {
    local method="$1" path="$2" principal="$3" body="${4:-}"
    local url="${_AUTHORITY_V1_BASE}${path}"
    local curl_args=(-s -o "$_RESPONSE_FILE" -w '%{http_code}' -X "$method"
        -H "x-avernet-principal: $principal"
        -H "Content-Type: application/json")
    if [[ -n "$body" ]]; then
        curl_args+=(-d "$body")
    fi
    HTTP_STATUS=$(curl "${curl_args[@]}" "$url" 2>/dev/null) || HTTP_STATUS="000"
    RESPONSE=$(cat "$_RESPONSE_FILE")
    _AUTHORITY_LAST_URL="$url"
}

# Materialize one trusted Human row through the mock registration lane
# (identity materialization only — the BCS_AUTH_MOCK lane is not an
# authorization path in any story below).
_authority_ensure_human() {
    local staff_no="$1"
    curl -s -o /dev/null -X POST \
        -H "X-Mock-User-Id: $staff_no" \
        -H "X-Mock-Nick-Name: $staff_no" \
        -H "Content-Type: application/json" -d '{}' \
        "${BCS_API_BASE_URL%/}/me/ensure-human" 2>/dev/null
}

_authority_json_field() {
    local json="$1" path="$2"
    printf '%s' "$json" | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
    for part in sys.argv[1].split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    print(__import__("json").dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else value)
except Exception:
    print("")
' "$path"
}

# ---------------------------------------------------------------------------
# Stories
# ---------------------------------------------------------------------------

# mine labels — both entrypoints serialize access_relation for every item.
story_bot_authority_mine_labels() {
    info "Story: every mine item carries an explicit access_relation label"
    local principal
    principal=$(_authority_principal_token "$BCS_MOCK_USER_ID") || {
        skip_case "no Gateway-Principal signing key in the environment; cannot mint the formal Human credential" || return 77
    }

    _authority_v1_request GET "/bots/mine?limit=100" "$principal"
    if [[ "$HTTP_STATUS" != "200" ]]; then
        fail "v1 mine returned $HTTP_STATUS"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return 1
    fi
    pass "v1 mine answered over the Gateway-Principal lane"
    TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))

    local unlabeled
    unlabeled=$(printf '%s' "$RESPONSE" | python3 -c '
import json, sys
try:
    items = json.load(sys.stdin)["data"]["items"]
    print(sum(1 for item in items if item.get("access_relation") not in ("owner", "manager")))
except Exception:
    print("error")
')
    if [[ "$unlabeled" == "0" ]]; then
        pass "every v1 mine item carries owner/manager access_relation"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "mine items without a strict access_relation label: $unlabeled (:RESPONSE $(printf '%s' "$RESPONSE" | head -c 200))"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    api_get "/bots/my?limit=100"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        local legacy_unlabeled
        legacy_unlabeled=$(printf '%s' "$RESPONSE" | python3 -c '
import json, sys
try:
    items = json.load(sys.stdin)["items"]
    print(sum(1 for item in items if item.get("access_relation") not in ("owner", "manager")))
except Exception:
    print("error")
')
        if [[ "$legacy_unlabeled" == "0" ]]; then
            pass "legacy /bots/my labels match (owner/manager on every item)"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            fail "legacy /bots/my items without the label: $legacy_unlabeled"
            TESTS_FAILED=$((TESTS_FAILED + 1))
        fi
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        warn "legacy /bots/my returned $HTTP_STATUS; label parity untested"
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi
}

# The manager list/grant/revoke lane over a freshly registered bot.
story_bot_manager_grant_and_revoke() {
    info "Story: owner grants a manager, the manager appears in the list, revoke reports remaining team sources"
    local principal staff_b bot_token bot_id
    principal=$(_authority_principal_token "$BCS_MOCK_USER_ID") || {
        skip_case "no Gateway-Principal signing key; cannot mint the formal Human credential" || return 77
    }
    bot_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    if [[ -z "$bot_token" ]]; then
        skip_case "no CEO session token for a registration lane" || return 77
    fi

    # Register a fresh owned bot through the trusted onboard lane.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' -X POST \
        -H "Authorization: Bearer $bot_token" \
        -H "X-Mock-User-Id: $BCS_MOCK_USER_ID" \
        -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME" \
        -H "Content-Type: application/json" \
        -d '{"name":"authority-story-bot","summary":"e2e authority story","skills":[{"name":"chat"}],"domains":[],"scopes":[]}' \
        "${BCS_API_BASE_URL%/}/bots/onboard" 2>/dev/null) || HTTP_STATUS="000"
    RESPONSE=$(cat "$_RESPONSE_FILE")
    if [[ "$HTTP_STATUS" != "200" && "$HTTP_STATUS" != "201" ]]; then
        warn "onboard returned $HTTP_STATUS ($(printf '%s' "$RESPONSE" | head -c 160)) — may collide with an earlier run; continuing with the registered bot"
    fi
    bot_id=$(_authority_json_field "$RESPONSE" "data.bot_id")
    [[ -n "$bot_id" ]] || bot_id=$(_authority_json_field "$RESPONSE" "bot_id")
    if [[ -z "$bot_id" ]]; then
        # Fall back to discovering the registered bot through the directory.
        api_get "/bots?limit=100"
        bot_id=$(printf '%s' "$RESPONSE" | python3 -c '
import json, sys
try:
    bots = json.load(sys.stdin)
    data = bots.get("data", bots)
    items = data.get("items", data if isinstance(data, list) else [])
    for bot in items:
        if bot.get("name") == "authority-story-bot":
            print(bot.get("bot_id") or bot.get("id") or bot.get("uuid") or "")
            break
except Exception:
    pass
' 2>/dev/null)
    fi
    if [[ -z "$bot_id" ]]; then
        skip_case "authority-story-bot not discoverable after onboard" || return 77
    fi
    info "story bot id: $bot_id"

    staff_b="authority-manager-002"
    _authority_ensure_human "$staff_b"

    local owner_read_grant
    _authority_v1_request GET "/bots/$bot_id/managers" "$principal"
    assert_status "owner reads the new bot's manager page" "200"
    if [[ "$HTTP_STATUS" != "200" ]]; then
        warn "response: $(printf '%s' "$RESPONSE" | head -c 200)"
        return 1
    fi
    owner_read_grant=$(_authority_json_field "$RESPONSE" "data.owner_user_id")
    assert_eq "the manager page projects the single current owner" "$owner_read_grant" "$BCS_MOCK_USER_ID"

    _authority_v1_request PUT "/bots/$bot_id/managers/$staff_b" "$principal" '{}'
    if [[ "$HTTP_STATUS" == "200" ]]; then
        assert_status "owner grants the manager over the v1 route" "200"
        assert_eq "grant reports the fixed manager role" \
            "$(_authority_json_field "$RESPONSE" "data.role")" "manager"
    else
        fail "manager grant returned $HTTP_STATUS ($(printf '%s' "$RESPONSE" | head -c 200))"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return 1
    fi

    _authority_v1_request GET "/bots/$bot_id/managers" "$principal"
    local contains
    contains=$(printf '%s' "$RESPONSE" | python3 -c '
import json, sys
try:
    print("true" if any(item.get("user_id") == sys.argv[1] for item in json.load(sys.stdin)["data"]["items"]) else "false")
except Exception:
    print("false")
' "$staff_b")
    assert_eq "the granted manager appears in the list" "$contains" "true"

    _authority_v1_request DELETE "/bots/$bot_id/managers/$staff_b" "$principal"
    assert_status "owner revokes the non-team manager source" "200"
    assert_eq "revoke reports no remaining team sources" \
        "$(_authority_json_field "$RESPONSE" "data.remaining_team_sources")" "[]"

    # A second human cannot grant anything on an unseen bot (fail-closed).
    local stranger_principal
    stranger_principal=$(_authority_principal_token "authority-stranger-777")
    _authority_v1_request PUT "/bots/$bot_id/managers/authority-stranger-777" "$stranger_principal" '{}'
    assert_status "an unrelated human cannot grant itself a manager edge" "403"
}

# The confirmed ownership-transfer story, end to end over the HTTP contract.
story_ownership_transfer_confirmed() {
    info "Story: the confirmed transfer flips roles and releases the pending slot"
    local principal_a principal_b bot_token bot_id version transfer_id
    principal_a=$(_authority_principal_token "$BCS_MOCK_USER_ID") || {
        skip_case "no Gateway-Principal signing key; cannot mint the formal Human credential" || return 77
    }
    staff_b="authority-receiver-003"
    _authority_ensure_human "$staff_b"
    principal_b=$(_authority_principal_token "$staff_b") || {
        skip_case "receiver credential" || return 77
    }

    bot_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    if [[ -z "$bot_token" ]]; then
        skip_case "no CEO session token for a registration lane" || return 77
    fi
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' -X POST \
        -H "Authorization: Bearer $bot_token" \
        -H "X-Mock-User-Id: $BCS_MOCK_USER_ID" \
        -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME" \
        -H "Content-Type: application/json" \
        -d '{"name":"authority-transfer-story-bot","summary":"e2e transfer story","skills":[{"name":"chat"}],"domains":[],"scopes":[]}' \
        "${BCS_API_BASE_URL%/}/bots/onboard" 2>/dev/null) || HTTP_STATUS="000"
    RESPONSE=$(cat "$_RESPONSE_FILE")
    bot_id=$(_authority_json_field "$RESPONSE" "data.bot_id")
    [[ -n "$bot_id" ]] || bot_id=$(_authority_json_field "$RESPONSE" "bot_id")
    if [[ -z "$bot_id" ]]; then
        skip_case "authority-transfer-story-bot not registered" || return 77
    fi
    info "story bot id: $bot_id"

    _authority_v1_request GET "/bots/$bot_id/ownership" "$principal_a"
    assert_status "the current owner reads the ownership snapshot" "200"
    version=$(_authority_json_field "$RESPONSE" "data.ownership_version")
    if [[ -z "$version" || "$version" == "error" ]]; then
        fail "ownership snapshot must expose ownership_version: $(printf '%s' "$RESPONSE" | head -c 200)"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return 1
    fi

    local request_id
    request_id=$(python3 -c 'import uuid; print(uuid.uuid4())')
    _authority_v1_request POST "/bots/$bot_id/ownership-transfers" "$principal_a" \
        "{\"to_user_id\":\"$staff_b\",\"expected_owner_version\":$version,\"client_request_id\":\"$request_id\"}"
    if [[ "$HTTP_STATUS" != "201" ]]; then
        fail "transfer create returned $HTTP_STATUS ($(printf '%s' "$RESPONSE" | head -c 240))"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return 1
    fi
    assert_status "the owner creates the pending transfer (201 first commit)" "201"
    transfer_id=$(_authority_json_field "$RESPONSE" "data.transfer_id")
    assert_not_empty "the create returns the transfer id" "$transfer_id"

    local pending_total
    _authority_v1_request GET "/ownership-transfers?direction=received&status=pending" "$principal_b"
    assert_status "the receiver reads the pending-filtered inbox" "200"
    pending_total=$(_authority_json_field "$RESPONSE" "data.total")
    assert_eq "exactly one pending row matches the create" "$pending_total" "1"

    _authority_v1_request POST "/ownership-transfers/$transfer_id/accept" "$principal_b" '{}'
    if [[ "$HTTP_STATUS" != "200" ]]; then
        fail "accept returned $HTTP_STATUS ($(printf '%s' "$RESPONSE" | head -c 240))"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); return 1
    fi
    assert_status "the receiver accepts (200 committed receipt)" "200"
    assert_eq "the receipt is terminal accepted" \
        "$(_authority_json_field "$RESPONSE" "data.status")" "accepted"

    _authority_v1_request GET "/bots/$bot_id/ownership" "$principal_a"
    assert_status "the former owner still reads the snapshot" "200"
    assert_eq "ownership moved to the receiver" \
        "$(_authority_json_field "$RESPONSE" "data.owner_user_id")" "$staff_b"
    assert_eq "the version CAS-bumped" \
        "$(_authority_json_field "$RESPONSE" "data.ownership_version")" "$((version + 1))"

    _authority_v1_request GET "/ownership-transfers?direction=received&status=pending" "$principal_b"
    assert_status "the accepted transfer released the pending slot" "200"
    assert_eq "no pending row remains" "$(_authority_json_field "$RESPONSE" "data.total")" "0"

    # The former owner — now only a residual manager of this bot — cannot
    # initiate a new transfer (owner-only action, spec §11.2).
    request_id=$(python3 -c 'import uuid; print(uuid.uuid4())')
    _authority_v1_request POST "/bots/$bot_id/ownership-transfers" "$principal_a" \
        "{\"to_user_id\":\"$staff_b\",\"expected_owner_version\":$((version + 1)),\"client_request_id\":\"$request_id\"}"
    assert_status "the former owner cannot initiate the next transfer" "403"
}

# The trusted team-manager platform slice (credential-gated, opt-in mount).
story_team_manager_sources_platform_sync() {
    info "Story: the trusted platform syncs team manager sources through the credential-gated slice"
    local principal credential bot_token bot_id team_url
    principal=$(_authority_principal_token "$BCS_MOCK_USER_ID") || {
        skip_case "no Gateway-Principal signing key; cannot mint the formal Human credential" || return 77
    }
    bot_token="$(get_bot_token CEO 2>/dev/null || echo '')"
    if [[ -z "$bot_token" ]]; then
        skip_case "no CEO session token for a registration lane" || return 77
    fi
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' -X POST \
        -H "Authorization: Bearer $bot_token" \
        -H "X-Mock-User-Id: $BCS_MOCK_USER_ID" \
        -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME" \
        -H "Content-Type: application/json" \
        -d '{"name":"authority-team-story-bot","summary":"e2e team sync story","skills":[{"name":"chat"}],"domains":[],"scopes":[]}' \
        "${BCS_API_BASE_URL%/}/bots/onboard" 2>/dev/null) || HTTP_STATUS="000"
    RESPONSE=$(cat "$_RESPONSE_FILE")
    bot_id=$(_authority_json_field "$RESPONSE" "data.bot_id")
    [[ -n "$bot_id" ]] || bot_id=$(_authority_json_field "$RESPONSE" "bot_id")
    if [[ -z "$bot_id" ]]; then
        skip_case "authority-team-story-bot not registered" || return 77
    fi
    team_url="${BCS_API_BASE_URL%/}/api/v1/bots/$bot_id/manager-sources/teams/e2e-story-team"

    # Lane probe FIRST: a 404 means this deployment did not arm the lane
    # (the default singlebox config keeps it unmounted) — an honest skip.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' -X PUT \
        -H "Content-Type: application/json" \
        -d '{"operation":"sync","manager_user_ids":[],"idempotency_key":"e2e-probe"}' \
        "$team_url" 2>/dev/null) || HTTP_STATUS="000"
    if [[ "$HTTP_STATUS" == "404" ]]; then
        skip_case "team-manager sync lane not mounted in this deployment ([team_manager_sync] unset)" || return 77
    fi
    assert_status "an uncredentialed sync is rejected at the credential gate" "401"
    credential=$(_authority_team_credential) || {
        skip_case "no BCS_TEAM_MANAGER_SYNC_SIGNING_KEY material; cannot mint the platform credential" || return 77
    }

    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' -X PUT \
        -H "Authorization: Bearer $credential" \
        -H "Content-Type: application/json" \
        -d '{"operation":"sync","manager_user_ids":["authority-team-member-004","authority-team-member-004"],"idempotency_key":"e2e-sync-1"}' \
        "$team_url" 2>/dev/null) || HTTP_STATUS="000"
    assert_status "the verified snapshot sync commits" "200"

    # Single-member repairs: POST add + DELETE remove.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' -X POST \
        -H "Authorization: Bearer $credential" \
        -H "Content-Type: application/json" \
        -d '{"user_id":"authority-team-member-005","idempotency_key":"e2e-repair-add-1"}' \
        "$team_url/members" 2>/dev/null) || HTTP_STATUS="000"
    assert_status "the member-repair POST commits" "200"

    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' -X DELETE \
        -H "Authorization: Bearer $credential" \
        -H "Idempotency-Key: e2e-repair-remove-1" \
        "$team_url/members?user_id=authority-team-member-005" 2>/dev/null) || HTTP_STATUS="000"
    assert_status "the member-repair DELETE commits" "200"

    # A tampered payload under the same idempotency key is a 400/409, never
    # a silent second write; the exact code belongs to the Rust suites —
    # here the story asserts it is NOT a 2xx.
    HTTP_STATUS=$(curl -s -o "$_RESPONSE_FILE" -w '%{http_code}' -X POST \
        -H "Authorization: Bearer $credential" \
        -H "Content-Type: application/json" \
        -d '{"user_id":"authority-team-member-006","idempotency_key":"e2e-repair-add-1"}' \
        "$team_url/members" 2>/dev/null) || HTTP_STATUS="000"
    if [[ "$HTTP_STATUS" != "2"* ]]; then
        pass "idempotency replays with a different payload never commit twice ($HTTP_STATUS)"
        TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        fail "a same-key different-payload repair returned $HTTP_STATUS"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi
}

# Maintenance-binary operator probe (the binary is NOT a bcs-cli leaf and
# never claimed to be — the runbook exit-code contract is what is probed).
story_ownership_migrate_maintenance_binary_probes() {
    info "Story: the governed maintenance binary demands --maintenance (usage exit code 2)"
    local migrate_bin
    migrate_bin="$(command -v bcs-ownership-migrate 2>/dev/null || true)"
    if [[ -z "$migrate_bin" ]]; then
        # Usual local layout: the workspace target dir next to the CLI.
        local guess="${BCS_MIGRATE_BIN:-}"
        if [[ -n "$guess" && -x "$guess" ]]; then
            migrate_bin="$guess"
        fi
    fi
    if [[ -z "$migrate_bin" ]]; then
        skip_case "bcs-ownership-migrate not on PATH (set BCS_MIGRATE_BIN to probe the usage contract)" || return 77
    fi

    set +e
    "$migrate_bin" inspect >/dev/null 2>&1
    local bare_status=$?
    set -e
    assert_eq "without --maintenance the binary refuses with the usage exit code" "$bare_status" "2"

    set +e
    "$migrate_bin" --maintenance --help >/dev/null 2>&1
    local help_status=$?
    set -e
    if [[ "$help_status" == "0" || "$help_status" == "2" ]]; then
        pass "the governed binary exposes its usage (--help exit=$help_status)"
        TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    else
        fail "--help exited $help_status"
        TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1))
    fi
}