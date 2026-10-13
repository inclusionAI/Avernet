#!/usr/bin/env bash
# Minimal local QwenWork validation stack. No desktop configuration mutations.
set -e
QWENWORK_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export FRONTEND_VARIANT=nextgen
source "${QWENWORK_SCRIPT_DIR}/singlebox.sh"
# Local overrides may select another variant; reselect before all lifecycle actions.
export FRONTEND_VARIANT=nextgen
frontend_select_variant

qwenwork_main() {
    local action="${1:-start}"
    case "$action" in
        -h|--help|help)
            echo "Usage: singlebox/qwenwork.sh [setup|start|restart|status|stop|check|verify]"
            echo "Starts only Backend, BCS, Gateway and the next-generation frontend."
            echo "Requires Python 3.11+ and the normal Singlebox prerequisites."
            return 0 ;;
        setup|start|restart|status|stop|check|verify) ;;
        *) log_error "Unsupported QwenWork action: $action"; return 1 ;;
    esac
    [ "$#" -le 1 ] || { log_error "Only one action is accepted; set ports in singlebox/.env.local"; return 1; }
    export FRONTEND_VARIANT=nextgen SERVER_ENV=local GATEWAY_AUTH_MOCK=1
    export bcs_server_url="http://127.0.0.1:${BCS_PORT}"
    export SINGLEBOX_QWENWORK_PROVIDER_STATE="${DEP_DIR}/standalone/qwenwork-provider.json"
    # Bypass inherited proxy settings only for this local validation stack.
    export HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= http_proxy= https_proxy= all_proxy=
    export NO_PROXY="127.0.0.1,localhost" no_proxy="127.0.0.1,localhost"
    if [[ "$action" == start || "$action" == restart || "$action" == verify ]] && [ -z "${QWENWORK_PYTHON:-}" ]; then
        if python3 -c 'import tomllib' >/dev/null 2>&1; then
            QWENWORK_PYTHON="$(command -v python3)"
        else
            QWENWORK_PYTHON="$(uv python find 3.12)" || return 1
        fi
    fi
    export QWENWORK_PYTHON
    local helper="${SCRIPT_DIR}/qwenwork_provider.py"
    local config
    case "$action" in
        setup|check) main --standalone --bcs-env local "$action" backend bcs gateway frontend ;;
        status|stop) main --standalone --bcs-env local "$action" frontend gateway bcs backend ;;
        verify) "$QWENWORK_PYTHON" "$helper" verify --state "$SINGLEBOX_QWENWORK_PROVIDER_STATE" --url "http://127.0.0.1:${GATEWAY_PORT}" ;;
        start|restart)
            "$QWENWORK_PYTHON" -c 'import tomllib' || { log_error "Python 3.11+ is required"; return 1; }
            main --standalone --bcs-env local "$action" backend bcs gateway
            config="$(resolve_bcs_config_file "${BCS_CONFIG_DIR:-${DEP_DIR}/standalone/bcs-config}")"
            "$QWENWORK_PYTHON" "$helper" ensure --state "$SINGLEBOX_QWENWORK_PROVIDER_STATE" --url "http://127.0.0.1:${BCS_PORT}"
            if ! "$QWENWORK_PYTHON" "$helper" configured --state "$SINGLEBOX_QWENWORK_PROVIDER_STATE" --config "$config"; then
                main --standalone --bcs-env local restart bcs
            fi
            "$QWENWORK_PYTHON" "$helper" verify --state "$SINGLEBOX_QWENWORK_PROVIDER_STATE" --url "http://127.0.0.1:${GATEWAY_PORT}"
            export QWENWORK_PROVIDER_SLUG=local
            # Restart so a previous dev-server process cannot retain disabled capability.
            main --standalone --bcs-env local restart frontend
            log_info "QwenWork stack ready: http://127.0.0.1:${FRONTEND_PORT}/bot-workshop"
            log_info "Local login: http://127.0.0.1:${GATEWAY_PORT}/_dev/login?next=${FRONTEND_PORT}"
            ;;
    esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    qwenwork_main "$@"
fi
