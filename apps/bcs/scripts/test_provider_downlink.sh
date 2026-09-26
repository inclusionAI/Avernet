#!/usr/bin/env bash
# Run the Provider Downlink automated test suite.
#
# Coverage:
# - Provider registration and admin-token isolation.
# - Provider Bot registration for static_bearer and agentpass.
# - HTTP Provider transport request body/auth/header contracts.
# - Provider Bot delivery target resolution in message-flow and system messages.
# - Simulated downlink group messages through a real test BCS server.
# - Simulated Provider Bot final callback through /bot/events.
#
# Usage:
#   ./scripts/test_provider_downlink.sh
#   ./scripts/test_provider_downlink.sh --quick
#   ./scripts/test_provider_downlink.sh --full --nocapture
#   ./scripts/test_provider_downlink.sh --list

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BCS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CARGO_BIN="${CARGO:-cargo}"

MODE="full"
NO_CAPTURE=0
KEEP_GOING=0
LIST_ONLY=0

usage() {
    cat <<'USAGE'
Usage: test_provider_downlink.sh [OPTIONS]

Options:
  --full          Run the full Provider Downlink suite (default).
  --quick         Run the minimum high-signal suite.
  --keep-going    Continue after failures and report a final summary.
  --nocapture     Pass --nocapture to cargo tests.
  --list          Print the test groups without running them.
  -h, --help      Show this help.

Environment:
  CARGO           Cargo binary to use. Defaults to "cargo".
USAGE
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --full)
            MODE="full"
            ;;
        --quick)
            MODE="quick"
            ;;
        --keep-going)
            KEEP_GOING=1
            ;;
        --nocapture)
            NO_CAPTURE=1
            ;;
        --list)
            LIST_ONLY=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

PASSED=0
FAILED=0
STARTED_AT="$(date +%s)"

print_header() {
    echo "Provider Downlink test suite"
    echo "  root: $BCS_ROOT"
    echo "  mode: $MODE"
    echo
}

run_case() {
    local label="$1"
    shift
    local -a cmd=("$@")
    if [ "$NO_CAPTURE" -eq 1 ] && [ "${cmd[1]:-}" = "test" ]; then
        cmd+=("--" "--nocapture")
    fi

    echo "==> $label"
    printf '    command:'
    printf ' %q' "${cmd[@]}"
    echo

    if (cd "$BCS_ROOT" && "${cmd[@]}"); then
        PASSED=$((PASSED + 1))
        echo "    [PASS] $label"
        echo
    else
        FAILED=$((FAILED + 1))
        echo "    [FAIL] $label"
        echo
        if [ "$KEEP_GOING" -ne 1 ]; then
            exit 1
        fi
    fi
}

list_quick_cases() {
    cat <<'CASES'
Quick suite:
  1. HTTP routes: provider registration, bot registration, admin-token isolation.
  2. /bot/events: static_bearer, agentpass, run-context and terminal handling.
  3. Bootstrap integration: register Provider/Bot, send group message, receive webhook, callback final.
CASES
}

list_full_cases() {
    list_quick_cases
    cat <<'CASES'
Full suite additions:
  4. Provider core: owner validation, duplicate refs, disabled routing, delivery target resolution.
  5. Provider store: provider, credential and binding repo contracts.
  6. Bot store: batched Provider Bot registration persists owner/token/index.
  7. HTTP Provider transport: Bearer auth, chat.send body, chat.history body and id semantics.
  8. Message-flow: provider target routing for history, web send and persistent group send.
  9. System messages: provider targets are resolved before delivery.
 10. Compile check for the BCS bootstrap package.
CASES
}

run_quick_suite() {
    run_case \
        "HTTP routes: provider registration and Provider Bot registration" \
        "$CARGO_BIN" test -p bcs-http --test provider_routes_contract

    run_case \
        "/bot/events: Provider Bot final callback auth and run-context handling" \
        "$CARGO_BIN" test -p bcs-http --test bot_events_contract

    run_case \
        "Bootstrap integration: downlink group message and Provider final callback" \
        "$CARGO_BIN" test -p bcs --test provider_downlink_integration
}

run_full_suite() {
    run_case \
        "Provider core: registration, owner validation and delivery target resolution" \
        "$CARGO_BIN" test -p bcs-bot --test provider_core

    run_case \
        "Provider store: provider, credential and binding contracts" \
        "$CARGO_BIN" test -p bcs-bot-store --test provider_repo_contract

    run_case \
        "Bot store: Provider Bot batched registration persists owner/token/index" \
        "$CARGO_BIN" test -p bcs-bot-store register_with_owner_and_token_persists_owner_token_and_index

    run_case \
        "HTTP routes: provider registration and Provider Bot registration" \
        "$CARGO_BIN" test -p bcs-http --test provider_routes_contract

    run_case \
        "/bot/events: Provider Bot final callback auth and run-context handling" \
        "$CARGO_BIN" test -p bcs-http --test bot_events_contract

    run_case \
        "HTTP Provider transport: webhook body, auth header and history response" \
        "$CARGO_BIN" test -p bcs-provider-http --test provider_transport_contract

    run_case \
        "Message-flow: provider history and delivery targets" \
        "$CARGO_BIN" test -p bcs-message-flow --test contract_message_flow provider

    run_case \
        "Message-flow: accepted chat.send records callback run context" \
        "$CARGO_BIN" test -p bcs-message-flow --test contract_message_flow accepted_chat_send_records_run_context_for_callback

    run_case \
        "System messages: resolve Provider delivery target" \
        "$CARGO_BIN" test -p bcs-system-message dispatch_resolves_provider_delivery_target

    run_case \
        "Bootstrap integration: downlink group message and Provider final callback" \
        "$CARGO_BIN" test -p bcs --test provider_downlink_integration

    run_case \
        "Compile check: BCS bootstrap" \
        "$CARGO_BIN" check -p bcs
}

print_summary() {
    local ended_at elapsed
    ended_at="$(date +%s)"
    elapsed=$((ended_at - STARTED_AT))
    echo "Provider Downlink suite summary"
    echo "  passed:  $PASSED"
    echo "  failed:  $FAILED"
    echo "  elapsed: ${elapsed}s"
}

print_header
if [ "$LIST_ONLY" -eq 1 ]; then
    if [ "$MODE" = "quick" ]; then
        list_quick_cases
    else
        list_full_cases
    fi
    exit 0
fi

if [ "$MODE" = "quick" ]; then
    run_quick_suite
else
    run_full_suite
fi

print_summary
if [ "$FAILED" -ne 0 ]; then
    exit 1
fi
