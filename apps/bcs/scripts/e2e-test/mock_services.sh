#!/usr/bin/env bash
# Lifecycle helper for the local Provider/Judge mock used by coverage E2E.

BCS_E2E_MOCK_OWNED="${BCS_E2E_MOCK_OWNED:-0}"
BCS_E2E_MOCK_PID="${BCS_E2E_MOCK_PID:-}"
BCS_E2E_MOCK_SERVICES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bcs_e2e_mock_start() {
    local runtime_dir="$1"
    if [[ -n "${BCS_E2E_MOCK_BASE_URL:-}" ]]; then
        if curl --noproxy '*' -fsS "${BCS_E2E_MOCK_BASE_URL}/health" >/dev/null 2>&1; then
            BCS_E2E_MOCK_OWNED=0
            export BCS_E2E_MOCK_OWNED
            return 0
        fi
        echo "BCS E2E mock is not healthy at ${BCS_E2E_MOCK_BASE_URL}" >&2
        return 1
    fi

    local ready_file log_file
    ready_file="${runtime_dir}/ready"
    log_file="${runtime_dir}/mock.log"
    mkdir -p "$runtime_dir"
    rm -f "$ready_file" "$log_file"
    python3 "${BCS_E2E_MOCK_SERVICES_DIR}/http_provider_judge_mock.py" \
        --ready-file "$ready_file" >"$log_file" 2>&1 &
    BCS_E2E_MOCK_PID=$!
    BCS_E2E_MOCK_OWNED=1

    local attempt
    for attempt in $(seq 1 100); do
        if [[ -s "$ready_file" ]]; then
            BCS_E2E_MOCK_BASE_URL="$(tr -d '\r\n' < "$ready_file")"
            if curl --noproxy '*' -fsS "${BCS_E2E_MOCK_BASE_URL}/health" >/dev/null 2>&1; then
                export BCS_E2E_MOCK_BASE_URL BCS_E2E_MOCK_PID BCS_E2E_MOCK_OWNED
                export BCS_E2E_JUDGE_API_KEY="local-e2e-key"
                echo "BCS E2E Provider/Judge mock: ${BCS_E2E_MOCK_BASE_URL}"
                return 0
            fi
        fi
        if ! kill -0 "$BCS_E2E_MOCK_PID" 2>/dev/null; then
            break
        fi
        sleep 0.05
    done

    echo "BCS E2E Provider/Judge mock failed to start; log: ${log_file}" >&2
    tail -n 80 "$log_file" >&2 || true
    bcs_e2e_mock_stop
    return 1
}

bcs_e2e_mock_stop() {
    if [[ "${BCS_E2E_MOCK_OWNED:-0}" != "1" || -z "${BCS_E2E_MOCK_PID:-}" ]]; then
        return 0
    fi
    kill "$BCS_E2E_MOCK_PID" 2>/dev/null || true
    wait "$BCS_E2E_MOCK_PID" 2>/dev/null || true
    BCS_E2E_MOCK_OWNED=0
    BCS_E2E_MOCK_PID=""
    export BCS_E2E_MOCK_OWNED BCS_E2E_MOCK_PID
}
