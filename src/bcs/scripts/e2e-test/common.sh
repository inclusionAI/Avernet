#!/bin/bash
# common.sh — Shared library for BCS e2e tests. Sourced, not executed directly.

# ============================================================================
# Environment
# ============================================================================

BCS_API_BASE_URL="${BCS_API_BASE_URL:-http://127.0.0.1:21000}"
# Mock caller identity. Defaults must match singlebox's bcs/bots modules
# (scripts/modules/bcs.sh, scripts/modules/bots.sh), which start BCS with
# BCS_MOCK_USER_ID=001 / admin. Override via env if BCS was started with a
# different mock user (e.g. BCS_MOCK_USER_ID=xxx ./e2e.sh).
BCS_MOCK_USER_ID="${BCS_MOCK_USER_ID:-001}"
BCS_MOCK_USER_NICK_NAME="${BCS_MOCK_USER_NICK_NAME:-admin}"

# Bot IDs (must match the default 5bots_profile started by ./scripts/singlebox.sh --local start bcs_bots).
# Code names map to the 5bots_profile roles: CEO / 产品经理(PM) / 研发(ENG) / 验证(QA) / 客服(CS).
BOT_CEO_ID="${BOT_CEO_ID:-CEO}"
BOT_PM_ID="${BOT_PM_ID:-产品经理}"
BOT_ENG_ID="${BOT_ENG_ID:-研发}"
BOT_QA_ID="${BOT_QA_ID:-验证}"
BOT_CS_ID="${BOT_CS_ID:-客服}"

# ============================================================================
# Colors
# ============================================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }

# ============================================================================
# Counters
# ============================================================================

TESTS_PASSED=0
TESTS_FAILED=0
TESTS_TOTAL=0
RESPONSE=""
RESPONSE_HEADERS=""
BCS_CLI_STDOUT=""
BCS_CLI_STDERR=""
BCS_CLI_EXIT=0

# ============================================================================
# Assertion Helpers
# ============================================================================

assert_ok() {
    local desc="$1"; shift
    if "$@" &>/dev/null; then
        pass "$desc"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "$desc"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

assert_eq() {
    local desc="$1" actual="$2" expected="$3"
    if [ "$actual" = "$expected" ]; then
        pass "$desc"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "$desc (expected='$expected', actual='$actual')"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

assert_not_empty() {
    local desc="$1" value="$2"
    if [ -n "$value" ]; then
        pass "$desc"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "$desc (value is empty)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

assert_contains() {
    local desc="$1" haystack="$2" needle="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        pass "$desc"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        fail "$desc ('$needle' not found)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
}

assert_status() {
    local desc="$1" expected="$2"
    assert_eq "$desc" "$HTTP_STATUS" "$expected"
}

assert_json_eq() {
    local desc="$1" json="$2" path="$3" expected="$4"
    local actual
    actual=$(printf '%s' "$json" | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
    for part in sys.argv[1].split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    if isinstance(value, bool):
        print("true" if value else "false")
    elif value is None:
        print("null")
    elif isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        print(value)
except Exception:
    print("__JSON_PATH_ERROR__")
' "$path")
    assert_eq "$desc" "$actual" "$expected"
}

assert_json_not_empty() {
    local desc="$1" json="$2" path="$3"
    local actual
    actual=$(printf '%s' "$json" | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
    for part in sys.argv[1].split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    if value is None or value == "" or value == [] or value == {}:
        print("")
    elif isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        print(value)
except Exception:
    print("")
' "$path")
    assert_not_empty "$desc" "$actual"
}

assert_json_array_contains() {
    local desc="$1" json="$2" path="$3" expected="$4"
    local found
    found=$(printf '%s' "$json" | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
    for part in sys.argv[1].split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    print("1" if sys.argv[2] in value else "0")
except Exception:
    print("0")
' "$path" "$expected")
    assert_eq "$desc" "$found" "1"
}

# ============================================================================
# Utility Helpers
# ============================================================================

json_field() {
    local json="$1" key="$2"
    python3 -c "
import json, sys
try:
    d = json.loads('''$json''')
    val = d.get('$key', '')
    print(val if val is not None else '')
except: print('')
"
}

json_field_default() {
    local json="$1" key="$2" default="${3:-}"
    local val
    val=$(json_field "$json" "$key")
    if [ -z "$val" ]; then
        echo "$default"
    else
        echo "$val"
    fi
}

wait_for_health() {
    local max_secs="${1:-30}"
    info "Waiting for BCS health (max ${max_secs}s)..."
    for i in $(seq 1 "$max_secs"); do
        if curl -sf "$BCS_API_BASE_URL/health" >/dev/null 2>&1; then
            pass "BCS is healthy"
            return 0
        fi
        sleep 1
    done
    fail "BCS not healthy after ${max_secs}s"
    return 1
}

ensure_human() {
    info "Ensuring mock human actor ($BCS_MOCK_USER_ID)..."
    api_post "/me/ensure-human" '{}'
    if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "201" ]; then
        pass "Mock human actor ready"
        return 0
    fi
    fail "Failed to ensure human actor (HTTP $HTTP_STATUS)"
    return 1
}

summary() {
    echo ""
    info "=== Test Summary ==="
    pass "Passed: $TESTS_PASSED"
    if [ "$TESTS_FAILED" -gt 0 ]; then
        fail "Failed: $TESTS_FAILED"
    else
        info "Failed: 0"
    fi
    info "Total:  $TESTS_TOTAL"
}

# ============================================================================
# HTTP Helpers
# ============================================================================

# Temp file for response body (persists across subshell boundaries).
_RESPONSE_FILE=$(mktemp)
_RESPONSE_HEADERS_FILE=$(mktemp)
trap 'rm -f "$_RESPONSE_FILE" "$_RESPONSE_HEADERS_FILE"' EXIT

# Generic request helper. Sets globals:
#   HTTP_STATUS — the HTTP status code (e.g. "200")
#   RESPONSE   — the response body
# Does NOT print to stdout so callers don't need $().
_api_request() {
    local method="$1" path="$2" body="${3:-}"
    local url="${BCS_API_BASE_URL}${path}"
    local curl_args=(-s -o "$_RESPONSE_FILE" -D "$_RESPONSE_HEADERS_FILE" -w '%{http_code}' -X "$method"
        -H "X-Mock-User-Id: $BCS_MOCK_USER_ID"
        -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME"
        -H "Content-Type: application/json")
    if [ -n "$body" ]; then
        curl_args+=(-d "$body")
    fi
    HTTP_STATUS=$(curl "${curl_args[@]}" "$url" 2>/dev/null) || HTTP_STATUS="000"
    RESPONSE=$(cat "$_RESPONSE_FILE")
    RESPONSE_HEADERS=$(cat "$_RESPONSE_HEADERS_FILE")
}

# Request with caller-supplied headers. The usual mock-human headers are still
# present, so stories can add provider admin/runtime credentials without
# losing the local human identity used by owner checks.
# Usage: api_request_headers METHOD PATH BODY "Header: value" ...
api_request_headers() {
    local method="$1" path="$2" body="$3"; shift 3
    local url="${BCS_API_BASE_URL}${path}"
    local curl_args=(-s -o "$_RESPONSE_FILE" -D "$_RESPONSE_HEADERS_FILE" -w '%{http_code}' -X "$method"
        -H "X-Mock-User-Id: $BCS_MOCK_USER_ID"
        -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME"
        -H "Content-Type: application/json")
    while [[ "$#" -gt 0 ]]; do
        curl_args+=(-H "$1")
        shift
    done
    [[ -n "$body" ]] && curl_args+=(-d "$body")
    HTTP_STATUS=$(curl "${curl_args[@]}" "$url" 2>/dev/null) || HTTP_STATUS="000"
    RESPONSE=$(cat "$_RESPONSE_FILE")
    RESPONSE_HEADERS=$(cat "$_RESPONSE_HEADERS_FILE")
}

api_get() {
    _api_request GET "$1"
}

api_post() {
    _api_request POST "$1" "${2:-}"
}

api_put() {
    _api_request PUT "$1" "${2:-}"
}

api_delete() {
    _api_request DELETE "$1"
}

api_patch() {
    _api_request PATCH "$1" "${2:-}"
}

# Bot-token-authenticated request. Some endpoints (actor self-status PUT, sync
# bot chat, session create/member/chat) resolve the caller from its bot session
# token (X-BCS-Bot-Token / Bearer) rather than the mock-human headers _api_request
# sends. Sets HTTP_STATUS / RESPONSE like _api_request.
# Usage: bot_request <METHOD> <PATH> <BOT_NAME> [BODY]
bot_request() {
    local method="$1" path="$2" bot="$3" body="${4:-}"
    ensure_cli_token "$bot" >/dev/null || { HTTP_STATUS="000"; RESPONSE=""; return 1; }
    local url="${BCS_API_BASE_URL}${path}"
    local curl_args=(-s -o "$_RESPONSE_FILE" -D "$_RESPONSE_HEADERS_FILE" -w '%{http_code}' -X "$method"
        -H "X-BCS-Bot-Token: $BCS_CLI_TOKEN"
        -H "Content-Type: application/json")
    [ -n "$body" ] && curl_args+=(-d "$body")
    HTTP_STATUS=$(curl "${curl_args[@]}" "$url" 2>/dev/null) || HTTP_STATUS="000"
    RESPONSE=$(cat "$_RESPONSE_FILE")
    RESPONSE_HEADERS=$(cat "$_RESPONSE_HEADERS_FILE")
}

bot_get()    { bot_request GET    "$1" "$2"; }
bot_post()   { bot_request POST   "$1" "$2" "${3:-}"; }
bot_put()    { bot_request PUT    "$1" "$2" "${3:-}"; }
bot_patch()  { bot_request PATCH  "$1" "$2" "${3:-}"; }
bot_delete() { bot_request DELETE "$1" "$2"; }

# ============================================================================
# Bot UUID Resolution
# ============================================================================

# Resolve a bot name to its UUID by querying GET /bots.
# Usage: uuid=$(resolve_bot_uuid "CEO")
resolve_bot_uuid() {
    local name="$1"
    local url="${BCS_API_BASE_URL}/bots?limit=100"
    local body
    body=$(curl -s "$url") || true
    echo "$body" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    bots = data if isinstance(data, list) else data.get('bots', data.get('items', []))
    for b in bots:
        cap = b.get('capabilities', {})
        if cap.get('name') == '$name':
            print(b.get('bot_uuid', ''))
            sys.exit(0)
    print('')
except:
    print('')
"
}

# Resolve all bot UUIDs. Call after BCS is healthy and bots are onboarded.
# Sets BOT_CEO_UUID, BOT_PM_UUID, etc.
resolve_all_bot_uuids() {
    info "Resolving bot UUIDs..."
    BOT_CEO_UUID=$(resolve_bot_uuid "$BOT_CEO_ID")
    BOT_PM_UUID=$(resolve_bot_uuid "$BOT_PM_ID")
    BOT_ENG_UUID=$(resolve_bot_uuid "$BOT_ENG_ID")
    BOT_QA_UUID=$(resolve_bot_uuid "$BOT_QA_ID")
    BOT_CS_UUID=$(resolve_bot_uuid "$BOT_CS_ID")

    local failed=0
    for var in BOT_CEO_UUID BOT_PM_UUID BOT_ENG_UUID BOT_QA_UUID BOT_CS_UUID; do
        if [ -z "${!var}" ]; then
            fail "Could not resolve UUID for $var"
            failed=$((failed + 1))
        fi
    done
    if [ "$failed" -gt 0 ]; then
        fail "Some bot UUIDs not resolved. Are bots onboarded?"
        return 1
    fi
    pass "Bot UUIDs resolved (CEO=$BOT_CEO_UUID, PM=$BOT_PM_UUID, ...)"
}

# ============================================================================
# Group Cleanup
# ============================================================================

# Delete all normal groups driven by the given bot UUID. The BCS enforces a
# per-driver active-group cap (20), so leftover groups from previous e2e runs
# would make `POST /groups` return 400 ("already drives 20 active group(s)")
# and cascade-fail the member/label/visibility tests. Call this once during
# setup, after bot UUIDs are resolved, to start from a clean state.
cleanup_driver_groups() {
    local driver_uuid="$1"
    [ -n "$driver_uuid" ] || return 0
    info "Cleaning up existing groups driven by $driver_uuid..."
    api_get "/groups?limit=100&group_kind=all"
    [ "$HTTP_STATUS" = "200" ] || { warn "list groups returned $HTTP_STATUS; skip cleanup"; return 0; }
    local ids
    ids=$(echo "$RESPONSE" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    items = d.get('items', []) or []
    for g in items:
        if g.get('driver_bot') == '$driver_uuid':
            gid = g.get('id', '')
            if gid:
                print(gid)
except Exception:
    pass
")
    local count=0
    while IFS= read -r gid; do
        [ -n "$gid" ] || continue
        # DELETE /groups/{id} requires bot_id (the driver bot's UUID) as the
        # caller actor; api_delete doesn't take a query, so call curl directly.
        local code
        code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE \
            "${BCS_API_BASE_URL}/groups/${gid}?bot_id=${driver_uuid}" \
            -H "X-Mock-User-Id: $BCS_MOCK_USER_ID" \
            -H "X-Mock-Nick-Name: $BCS_MOCK_USER_NICK_NAME" 2>/dev/null) || code="000"
        if [ "$code" = "200" ]; then
            count=$((count + 1))
        fi
    done <<< "$ids"
    if [ "$count" -gt 0 ]; then
        pass "Cleaned up $count existing group(s) driven by $driver_uuid"
    else
        info "No existing groups to clean up"
    fi
}

# ============================================================================
# bcs-cli E2E helpers
# ============================================================================

# Ensure SCRIPT_DIR is set when common.sh is sourced standalone (e2e.sh
# normally sets it before sourcing common.sh). The helpers below derive
# repo_root from $SCRIPT_DIR/../../../.. .
if [[ -z "${SCRIPT_DIR:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

# Cached bcs-cli binary path and per-bot token (resolved once per run).
BCS_CLI_BIN_PATH=""
BCS_CLI_TOKEN=""

# Resolve the bcs-cli binary. Priority:
#   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
#   2. src/bcs/target/debug/bcs-cli
#   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
# Echoes the invocation prefix; returns 0 if a real binary is in hand, 1 on fallback-only.
get_bcs_cli_bin() {
    if [[ -n "${BCS_CLI_BIN:-}" && -x "${BCS_CLI_BIN:-}" ]]; then
        BCS_CLI_BIN_PATH="${BCS_CLI_BIN:-}"
        return 0
    fi
    local repo_root
    repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
    if [[ -x "$debug_bin" ]]; then
        BCS_CLI_BIN_PATH="$debug_bin"
        return 0
    fi
    warn "bcs-cli binary not found; falling back to 'cargo run' (slow; build first for speed)"
    BCS_CLI_BIN_PATH="cargo run -p bcs-cli --quiet --"
    return 1
}

# Resolve the bots' data root in the singlebox standalone layout.
# singlebox --standalone lays bots out as
#   .standalone-openclaw/profiles/<role-slug>/.bcs/session.json
# (slugs: ceo, product-manager, engineering, verification, customer-service).
# Override the root with $BCS_BOTS_DATA_DIR if the layout differs.
_get_bot_data_root() {
    if [[ -n "${BCS_BOTS_DATA_DIR:-}" ]]; then
        echo "$BCS_BOTS_DATA_DIR"
        return
    fi
    local repo_root
    repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
    echo "$repo_root/.standalone-openclaw/profiles"
}

# Echo the driven bot's profile directory (the dir containing .bcs/session.json
# whose bot_uuid matches BOT_<NAME>_UUID). Empty if unresolved. bcs_cli sets
# BOT_DATA_DIR to this for the call so self-resolving subcommands (visibility,
# friend, session, connect) resolve the correct bot without a global side effect.
_get_bot_data_dir() {
    local bot_name="$1"
    local bot_uuid_var="BOT_${bot_name}_UUID"
    local bot_uuid="${!bot_uuid_var:-}"
    if [[ -z "$bot_uuid" ]]; then
        echo ""
        return
    fi
    local root session_file
    root="$(_get_bot_data_root)"
    for session_file in "$root"/*/.bcs/session.json; do
        [[ -f "$session_file" ]] || continue
        local match
        match="$(python3 -c "
import json, sys
try:
    d=json.load(open(sys.argv[1]))
    print('1' if d.get('bot_uuid')==sys.argv[2] else '0')
except Exception:
    print('0')
" "$session_file" "$bot_uuid")"
        if [[ "$match" == "1" ]]; then
            # session_file = <root>/<slug>/.bcs/session.json -> profile dir = <root>/<slug>
            dirname "$(dirname "$session_file")"
            return
        fi
    done
    echo ""
}

# Echo a bot's BCS token from its session.json; empty string if unavailable.
# Requires resolve_all_bot_uuids to have run first.
# Usage: token=$(get_bot_token CEO)
get_bot_token() {
    local bot_name="$1"
    local dir
    dir="$(_get_bot_data_dir "$bot_name")"
    if [[ -z "$dir" ]]; then
        echo ""
        return
    fi
    python3 -c "
import json, sys
try:
    d=json.load(open(sys.argv[1] + '/.bcs/session.json'))
    print(d.get('token','') or '')
except Exception:
    print('')
" "$dir"
}

# Ensure BCS_CLI_TOKEN is populated for the given bot; return 1 (skip) if not.
# Always fetches fresh per call — do NOT cache across bots (a cached CEO token
# would be reused for PM and auth the wrong identity on self-resolving commands).
ensure_cli_token() {
    local bot="$1"
    BCS_CLI_TOKEN="$(get_bot_token "$bot")"
    if [[ -z "$BCS_CLI_TOKEN" ]]; then
        warn "no token for '$bot' (session.json not found); skipping bcs-cli auth case"
        return 1
    fi
    return 0
}

# Mark the current case as skipped with a warn log; returns 77 so the caller
# can `skip_case "reason" || return 77` without incrementing failure counters.
skip_case() {
    warn "SKIP: $1"
    return 77
}

# Extract a top-level field from a JSON string via python3. Usage:
#   val=$(_cli_json_field "$json" "group_id")
_cli_json_field() {
    local json="$1" key="$2"
    printf '%s' "$json" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    v=d.get(sys.argv[1],'') if len(sys.argv)>1 else ''
    print(v if v is not None else '')
except Exception:
    print('')
" "$key"
}

# True if $1 (JSON/string) contains substring $2.
_cli_contains() { [[ "$1" == *"$2"* ]]; }

# Run bcs-cli as <bot> with <args>. Injects --base-url at top level and
# --token right after the subcommand name (token is a per-subcommand clap arg,
# not top-level). Captures stdout into BCS_CLI_STDOUT and exit code into
# BCS_CLI_EXIT; returns bcs-cli's exit code.
#
# Usage: bcs_cli CEO list            -> bcs-cli list --token T        (MOLTIS_BCS_URL env set)
#        bcs_cli CEO get "$uuid"     -> bcs-cli get --token T "$uuid" (MOLTIS_BCS_URL env set)
# For token-less subcommands (health), pass bot="" — no --token is injected.
bcs_cli() {
    local bot="$1"; shift
    local bin
    get_bcs_cli_bin >/dev/null
    bin="$BCS_CLI_BIN_PATH"
    local sub="$1" nested="${2:-}" third="${3:-}" command_path
    local args=() global_args=()
    [[ "${BCS_CLI_FORCE_JSON:-0}" = "1" ]] && global_args+=(--json)
    # bcs-cli's top-level URL flag is -u/--url (NOT --base-url), and
    # confirm-group-help reuses --url/<URL> for the confirm URL. To avoid any
    # flag-namespace collision, inject the base URL via the MOLTIS_BCS_URL env
    # var (which bcs-cli reads for -u/--url) instead of a CLI flag.
    export MOLTIS_BCS_URL="$BCS_API_BASE_URL"
    if [[ -z "$sub" ]]; then
        BCS_CLI_STDOUT=""
        BCS_CLI_STDERR=""
        BCS_CLI_EXIT=2
        warn "bcs_cli: no subcommand given"
        return 2
    fi
    args+=("$sub")
    if [[ -n "$bot" ]]; then
        if [[ "$bot" == token:* ]]; then
            BCS_CLI_TOKEN="${bot#token:}"
        else
            ensure_cli_token "$bot" >/dev/null || { BCS_CLI_STDOUT=""; BCS_CLI_STDERR=""; BCS_CLI_EXIT=126; return 126; }
        fi
        args+=(--token "$BCS_CLI_TOKEN")
    fi
    shift
    args+=("$@")
    local out rc
    # Secure per-call temp file for stderr (replaces the insecure hardcoded
    # /tmp/bcs_cli.err — symlink/race safe via mktemp). Cleaned up on return.
    local err_file
    err_file="$(mktemp -t bcs_cli.err.XXXXXX 2>/dev/null || mktemp)"
    # Self-resolving subcommands (visibility/friend/session/connect) read the
    # bot UUID from $BOT_DATA_DIR/.bcs/session.json, not from --token. Set it
    # inline for THIS call only (no leak to the parent shell). For tokenless
    # calls (bot=""), leave the env untouched.
    local bot_dir=""
    if [[ -n "$bot" && "$bot" != token:* ]]; then
        bot_dir="$(_get_bot_data_dir "$bot")"
    fi
    command_path="$sub"
    case "$sub" in
        friend|channel|visibility|session|service)
            [[ -n "$nested" && "$nested" != -* ]] && command_path="$sub $nested"
            ;;
    esac
    # `session file <leaf>` is a 3-level command tree (session -> file ->
    # upload/list/download/delete/share/capabilities). The case above only
    # captures 2 levels ("session file"); promote to the full leaf path so the
    # CLI coverage gate (which discovers full leaf paths via recursive --help)
    # records each `session file <leaf>` invocation distinctly.
    if [[ "$command_path" == "session file" && -n "${third:-}" && "$third" != -* ]]; then
        command_path="session file $third"
    fi
    if [[ -n "${BCS_CLI_COVERAGE_LOG:-}" ]]; then
        printf '%s\n' "$command_path" >> "$BCS_CLI_COVERAGE_LOG"
    fi
    if [[ -n "$bot_dir" ]]; then
        out="$( BOT_DATA_DIR="$bot_dir" $bin ${global_args[@]+"${global_args[@]}"} "${args[@]}" 2>"$err_file" )" && rc=$? || rc=$?
    else
        out="$( $bin ${global_args[@]+"${global_args[@]}"} "${args[@]}" 2>"$err_file" )" && rc=$? || rc=$?
    fi
    BCS_CLI_STDOUT="$out"
    BCS_CLI_STDERR="$(cat "$err_file")"
    BCS_CLI_EXIT="$rc"
    if [[ "$rc" -ne 0 ]] && [[ -s "$err_file" ]]; then
        warn "bcs-cli $command_path exited $rc: $(printf '%s' "$BCS_CLI_STDERR" | head -c 200)"
    fi
    rm -f "$err_file"
    return "$rc"
}

# Run bcs-cli with structured JSON output while preserving the regular
# human-readable behavior used by existing CLI assertions.
bcs_cli_json() {
    BCS_CLI_FORCE_JSON=1 bcs_cli "$@"
}
