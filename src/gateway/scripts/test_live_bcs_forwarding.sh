#!/usr/bin/env bash
# Start an isolated loopback BCS and run the opt-in Gateway forwarding proof.
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
bcs_dir="${root_dir}/src/bcs"
gateway_dir="${root_dir}/src/gateway"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/gateway-live-bcs.XXXXXX")"
config_file="${work_dir}/bcs-config-local.toml"
log_file="${work_dir}/bcs.log"
port="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
bcs_url="http://127.0.0.1:${port}"
bcs_pid=""
watchdog_pid=""
development_signing_key="avernet-dev-signing-key-NOT-FOR-PROD"
group_session_signing_key="gateway-live-group-session-key-at-least-32-bytes"

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

cleanup() {
    status=$?
    if [[ -n "${watchdog_pid}" ]] && kill -0 "${watchdog_pid}" 2>/dev/null; then
        kill "${watchdog_pid}" 2>/dev/null || true
        wait "${watchdog_pid}" 2>/dev/null || true
    fi
    if [[ -n "${bcs_pid}" ]] && kill -0 "${bcs_pid}" 2>/dev/null; then
        kill "${bcs_pid}" 2>/dev/null || true
        for _ in {1..10}; do
            kill -0 "${bcs_pid}" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "${bcs_pid}" 2>/dev/null; then
            kill -9 "${bcs_pid}" 2>/dev/null || true
        fi
        wait "${bcs_pid}" 2>/dev/null || true
    fi
    if [[ ${status} -ne 0 && -f "${log_file}" ]]; then
        echo "BCS log (${log_file}):" >&2
        tail -n 200 "${log_file}" >&2 || true
    fi
    rm -rf "${work_dir}"
    exit "${status}"
}
trap cleanup EXIT INT TERM

sed \
    -e "s#^port = 21000#port = ${port}#" \
    -e "s#^bots_base_dir = .*#bots_base_dir = \"${work_dir}/bots\"#" \
    -e "s#^bcs_endpoint = .*#bcs_endpoint = \"${bcs_url}\"#" \
    -e "s#^path = \"bcs.db\"#path = \"${work_dir}/bcs.db\"#" \
    -e "s#^base_dir = \"seeds/collaboration-templates\"#base_dir = \"${bcs_dir}/seeds/collaboration-templates\"#" \
    -e "s#^path = \"\./logs\"#path = \"${work_dir}/logs\"#" \
    -e "s#^data_dir = \"\./data/session-files\"#data_dir = \"${work_dir}/session-files\"#" \
    -e "s#^base_url = \"http://127.0.0.1:21000\"#base_url = \"${bcs_url}\"#" \
    -e "s#^share_base_url = \"http://127.0.0.1:21000\"#share_base_url = \"${bcs_url}\"#" \
    "${bcs_dir}/configs/bcs-config-local.toml" > "${config_file}"

(cd "${bcs_dir}" && CARGO_SHIM_SKIP_CLEAN=1 cargo build -p bcs --bin bcs)
env -u SERVER_ENV -u REAL_SERVER_ENV -u ALIPAY_APP_ENV \
    SERVER_ENV=local \
    AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE="${development_signing_key}" \
    BCS_SECRET_BCN_GROUP_SESSION_WS_JWT="${group_session_signing_key}" \
    "${bcs_dir}/target/debug/bcs" --config-dir "${config_file}" > "${log_file}" 2>&1 &
bcs_pid=$!

if ! command -v lsof >/dev/null 2>&1; then
    echo "lsof is required to verify BCS owns its selected loopback port" >&2
    exit 1
fi

bcs_owns_selected_port() {
    lsof -nP -a -p "${bcs_pid}" -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
}

health_check() {
    curl --connect-timeout 2 --max-time 5 --fail --silent --show-error --noproxy '*' \
        "${bcs_url}/health" >/dev/null
}

for _ in {1..60}; do
    if ! kill -0 "${bcs_pid}" 2>/dev/null; then
        echo "BCS exited before becoming healthy" >&2
        exit 1
    fi
    if bcs_owns_selected_port && health_check; then
        break
    fi
    sleep 1
done

if ! bcs_owns_selected_port; then
    echo "BCS process ${bcs_pid} never owned selected loopback port ${port}" >&2
    exit 1
fi

if ! health_check; then
    echo "BCS process ${bcs_pid} owns loopback port ${port} but did not become healthy within 60 seconds" >&2
    exit 1
fi

(
    cd "${gateway_dir}"
    GATEWAY_LIVE_BCS=1 GATEWAY_LIVE_BCS_URL="${bcs_url}" \
        uv run pytest -q tests/integration/test_live_bcs_forwarding.py
) &
test_pid=$!
(
    sleep 60
    if kill -0 "${test_pid}" 2>/dev/null; then
        echo "Gateway live-forwarding test exceeded 60 seconds" >&2
        kill "${test_pid}" 2>/dev/null || true
    fi
) &
watchdog_pid=$!

wait "${test_pid}"
kill "${watchdog_pid}" 2>/dev/null || true
wait "${watchdog_pid}" 2>/dev/null || true
watchdog_pid=""
