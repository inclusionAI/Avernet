#!/usr/bin/env bash
# src/proxy/docker-test/test-proxy.sh — build the proxy image and run a full
# bare-mode test.
#
# Flow:
#   1. Build proxy:local from docker/services/proxy.dockerfile
#   2. Start the proxy (SERVER_ENV=prod, test JWT secret)
#   3. Wait for the proxy /health endpoint and report success / failure
#
# Usage:
#   src/proxy/docker-test/test-proxy.sh            # build + up + health check
#   src/proxy/docker-test/test-proxy.sh up         # same as above
#   src/proxy/docker-test/test-proxy.sh build      # only rebuild proxy:local
#   src/proxy/docker-test/test-proxy.sh down       # tear down the test stack
#   src/proxy/docker-test/test-proxy.sh status     # show compose ps (and curl /health)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd "${SCRIPT_DIR}/../../../docker" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.proxy-test.yml"
PROJECT="proxytest"

export SANDBOXPROXY_PORT="${SANDBOXPROXY_PORT:-8888}"
export SANDBOXPROXY_JWT_SECRET="${SANDBOXPROXY_JWT_SECRET:-proxy-test-secret-not-for-prod}"
HOST_PORT="${HOST_PORT:-18889}"

log() { printf "\n==> %s\n" "$*"; }

build_image() {
  log "building proxy:local"
  "${DOCKER_DIR}/build-image.sh" services/proxy.dockerfile --image proxy --tag local
}

up_stack() {
  log "starting proxy (project: ${PROJECT})"
  docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" up -d
}

wait_healthy() {
  local url="http://127.0.0.1:${HOST_PORT}/health"
  local max="${HEALTH_WAIT_SECS:-60}"
  local i=0
  log "waiting for proxy /health at ${url} (up to ${max}s)"
  until curl -fsS "${url}" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "${i}" -ge "${max}" ]; then
      echo "error: proxy did not become healthy within ${max}s" >&2
      docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" ps
      echo "--- proxy logs ---" >&2
      docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" logs proxy --tail 40 >&2 || true
      return 1
    fi
    sleep 1
  done
  printf "\n==> proxy healthy: "
  curl -fsS "${url}"
  printf "\n"
}

show_status() {
  docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" ps
  echo
  curl -fsS "http://127.0.0.1:${HOST_PORT}/health" && echo || true
}

case "${1:-up}" in
  build)
    build_image
    ;;
  up)
    build_image
    up_stack
    wait_healthy
    ;;
  down)
    log "tearing down test stack"
    docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" down
    ;;
  status)
    show_status
    ;;
  *)
    echo "usage: $0 {build|up|down|status}" >&2
    exit 2
    ;;
esac