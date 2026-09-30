#!/usr/bin/env bash
set -euo pipefail

cd /opt/ocb

export HOME="${HOME:-/root}"
export HOST="${HOST:-0.0.0.0}"
export LOCAL_DEV_MODE="${LOCAL_DEV_MODE:-true}"
export SERVER_ENV="${SERVER_ENV:-local}"
export BCS_SERVER_ENV="${BCS_SERVER_ENV:-local}"
export BCS_AUTO_ONBOARD="${BCS_AUTO_ONBOARD:-1}"
export BCS_AUTH_MOCK="${BCS_AUTH_MOCK:-1}"
export BCS_MOCK_USER_ID="${BCS_MOCK_USER_ID:-11111111}"
export BCS_MOCK_USER_NICK_NAME="${BCS_MOCK_USER_NICK_NAME:-docker-local}"
export BCS_MOCK_USER_CHANNEL="${BCS_MOCK_USER_CHANNEL:-mock}"
export BCS_SKIP_BUILD_PREREQS="${BCS_SKIP_BUILD_PREREQS:-1}"
export BCS_LOCAL_ONBOARD_RETRIES="${BCS_LOCAL_ONBOARD_RETRIES:-5}"
export BCS_LOCAL_BOTS_READY_TIMEOUT="${BCS_LOCAL_BOTS_READY_TIMEOUT:-180}"
export BCS_PORT="${BCS_PORT:-21000}"
export BCS_BIND="${BCS_BIND:-0.0.0.0}"
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1}"
export no_proxy="${no_proxy:-localhost,127.0.0.1,::1}"

LOG_DIR="/opt/ocb/scripts/.dependencies/logs"
mkdir -p "${LOG_DIR}"
touch "${LOG_DIR}/bcs.log" "${LOG_DIR}/bcs_bots_stack.log" "${LOG_DIR}/frontend.log"
export BCS_LOG="${BCS_LOG:-${LOG_DIR}/bcs.log}"

CONFIG_DIR="/opt/ocb/scripts/.dependencies/configs"
mkdir -p "${CONFIG_DIR}"
export BCS_CONFIG_DIR="${BCS_CONFIG_DIR:-${CONFIG_DIR}}"

cleanup() {
  set +e
  if [ -n "${tail_pid:-}" ]; then
    kill "${tail_pid}" 2>/dev/null || true
  fi
  if [ -n "${frontend_pid:-}" ]; then
    kill "${frontend_pid}" 2>/dev/null || true
  fi
  OCB_LIFECYCLE_OWNER=entrypoint ./scripts/singlebox.sh stop bcs >/dev/null 2>&1 || true
}
trap cleanup INT TERM EXIT

OCB_LIFECYCLE_OWNER=entrypoint ./scripts/singlebox.sh --bcs-env local --bcs-auto-onboard start bcs
./scripts/singlebox.sh status bcs || true

export FRONTEND_PORT="${FRONTEND_PORT:-8000}"
export FRONTEND_BCS_TARGET="${FRONTEND_BCS_TARGET:-http://127.0.0.1:${BCS_PORT}}"
node /usr/local/bin/ocb-frontend-server >> "${LOG_DIR}/frontend.log" 2>&1 &
frontend_pid=$!

frontend_waited=0
while [ "${frontend_waited}" -lt 60 ]; do
  if lsof -tiTCP:"${FRONTEND_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
  frontend_waited=$((frontend_waited + 1))
done
if ! lsof -tiTCP:"${FRONTEND_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Frontend did not bind port ${FRONTEND_PORT}; see ${LOG_DIR}/frontend.log" >&2
  exit 1
fi

cat <<INFO

BCS local server is running.
  Frontend: http://127.0.0.1:${FRONTEND_PORT}
  BCS:      http://127.0.0.1:${BCS_PORT}
  Health:   http://127.0.0.1:${BCS_PORT}/health

5 local test bots are started by src/bcs/scripts/start_bcs_bots.sh and
auto-onboarded to BCS with visibility=public.

To connect from outside:
  - Use bcs-cli (mounted at /opt/ocb/src/bcs/target/debug/bcs-cli)
  - Or run OpenClaw on the host with the BCN plugin:
      BCS_URL=ws://127.0.0.1:${BCS_PORT}/ws/bot openclaw gateway run --port 18789

INFO

tail -F "${LOG_DIR}/bcs.log" "${LOG_DIR}/bcs_bots_stack.log" "${LOG_DIR}/frontend.log" &
tail_pid=$!
wait "${tail_pid}"
