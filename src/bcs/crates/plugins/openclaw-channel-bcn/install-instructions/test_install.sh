#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

run_install_with_fake_tools() {
    local workspace="$1"
    shift
    local tmp bindir
    tmp="$(mktemp -d)"
    bindir="${tmp}/bin"
    mkdir -p "$bindir"

    cat > "${bindir}/curl" <<'STUB'
#!/usr/bin/env bash
out_file=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o)
            out_file="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done
if [ -n "$out_file" ]; then
    cat > "$out_file" <<'JSON'
{"bot_uuid":"bot_test_001","bot_token":"token_test_001"}
JSON
fi
printf '200'
STUB
    chmod +x "${bindir}/curl"

    cat > "${bindir}/openclaw" <<'STUB'
#!/usr/bin/env bash
case "$1 $2" in
    "plugins list")
        printf 'BCS openclaw-channel-bcn enabled\n'
        ;;
    "plugins uninstall"|"plugins install"|"gateway restart")
        ;;
    "gateway status")
        printf 'running\n'
        ;;
esac
STUB
    chmod +x "${bindir}/openclaw"

    PATH="${bindir}:$PATH" OPENCLAW_WORKSPACE="$workspace" \
        bash "${SCRIPT_DIR}/install.sh" --token human_test --bot-name TestBot "$@" >/dev/null

    rm -rf "$tmp"
}

test_default_install_writes_openclaw_bcs_url() {
    local tmp workspace
    tmp="$(mktemp -d)"
    workspace="${tmp}/openclaw"
    mkdir -p "$workspace"

    run_install_with_fake_tools "$workspace"

    [ -f "${workspace}/openclaw.json" ] || fail "openclaw.json was not created"
    local bcs_url heartbeat
    bcs_url="$(jq -r '.channels.bcs.bcsUrl // empty' "${workspace}/openclaw.json")"
    heartbeat="$(jq -r '.channels.bcs.heartbeatIntervalMs // empty' "${workspace}/openclaw.json")"
    [ "$bcs_url" = "ws://127.0.0.1:21000/ws/bot" ] ||
        fail "expected default bcsUrl, got ${bcs_url:-<empty>}"
    [ "$heartbeat" = "30000" ] ||
        fail "expected default heartbeat, got ${heartbeat:-<empty>}"

    rm -rf "$tmp"
}

test_existing_openclaw_config_gets_bcs_url() {
    local tmp workspace
    tmp="$(mktemp -d)"
    workspace="${tmp}/openclaw"
    mkdir -p "$workspace"
    cat > "${workspace}/openclaw.json" <<'JSON'
{
  "gateway": {
    "port": 18789
  },
  "channels": {
    "bcs": {
      "enabled": true
    }
  }
}
JSON

    run_install_with_fake_tools "$workspace"

    local bcs_url gateway_port heartbeat
    bcs_url="$(jq -r '.channels.bcs.bcsUrl // empty' "${workspace}/openclaw.json")"
    gateway_port="$(jq -r '.gateway.port // empty' "${workspace}/openclaw.json")"
    heartbeat="$(jq -r '.channels.bcs.heartbeatIntervalMs // empty' "${workspace}/openclaw.json")"
    [ "$bcs_url" = "ws://127.0.0.1:21000/ws/bot" ] ||
        fail "expected existing config to get default bcsUrl, got ${bcs_url:-<empty>}"
    [ "$gateway_port" = "18789" ] ||
        fail "expected existing gateway port to be preserved, got ${gateway_port:-<empty>}"
    [ "$heartbeat" = "30000" ] ||
        fail "expected existing config to get default heartbeat, got ${heartbeat:-<empty>}"

    rm -rf "$tmp"
}

test_existing_bcs_url_is_preserved_by_default() {
    local tmp workspace
    tmp="$(mktemp -d)"
    workspace="${tmp}/openclaw"
    mkdir -p "$workspace"
    cat > "${workspace}/openclaw.json" <<'JSON'
{
  "channels": {
    "bcs": {
      "enabled": true,
      "bcsUrl": "wss://existing.example/ws/bot",
      "heartbeatIntervalMs": 15000
    }
  }
}
JSON

    run_install_with_fake_tools "$workspace"

    local bcs_url heartbeat
    bcs_url="$(jq -r '.channels.bcs.bcsUrl // empty' "${workspace}/openclaw.json")"
    heartbeat="$(jq -r '.channels.bcs.heartbeatIntervalMs // empty' "${workspace}/openclaw.json")"
    [ "$bcs_url" = "wss://existing.example/ws/bot" ] ||
        fail "expected existing bcsUrl to be preserved, got ${bcs_url:-<empty>}"
    [ "$heartbeat" = "15000" ] ||
        fail "expected existing heartbeat to be preserved, got ${heartbeat:-<empty>}"

    rm -rf "$tmp"
}

test_explicit_bcs_endpoint_overrides_existing_bcs_url() {
    local tmp workspace
    tmp="$(mktemp -d)"
    workspace="${tmp}/openclaw"
    mkdir -p "$workspace"
    cat > "${workspace}/openclaw.json" <<'JSON'
{
  "channels": {
    "bcs": {
      "enabled": true,
      "bcsUrl": "wss://existing.example/ws/bot"
    }
  }
}
JSON

    run_install_with_fake_tools "$workspace" --bcs-endpoint http://bcs.example:21000

    local bcs_url
    bcs_url="$(jq -r '.channels.bcs.bcsUrl // empty' "${workspace}/openclaw.json")"
    [ "$bcs_url" = "ws://bcs.example:21000/ws/bot" ] ||
        fail "expected explicit endpoint to override bcsUrl, got ${bcs_url:-<empty>}"

    rm -rf "$tmp"
}

test_trailing_slash_bcs_endpoint_writes_single_slash_url() {
    local tmp workspace
    tmp="$(mktemp -d)"
    workspace="${tmp}/openclaw"
    mkdir -p "$workspace"

    run_install_with_fake_tools "$workspace" --bcs-endpoint http://localhost:21000/

    local bcs_url
    bcs_url="$(jq -r '.channels.bcs.bcsUrl // empty' "${workspace}/openclaw.json")"
    [ "$bcs_url" = "ws://127.0.0.1:21000/ws/bot" ] ||
        fail "expected trailing slash endpoint to normalize bcsUrl, got ${bcs_url:-<empty>}"

    rm -rf "$tmp"
}

test_default_install_writes_openclaw_bcs_url
test_existing_openclaw_config_gets_bcs_url
test_existing_bcs_url_is_preserved_by_default
test_explicit_bcs_endpoint_overrides_existing_bcs_url
test_trailing_slash_bcs_endpoint_writes_single_slash_url
