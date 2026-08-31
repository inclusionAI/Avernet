#!/usr/bin/env bash
# src/gateway/docker-test/test-gateway.sh — build the gateway image and run a full prod-mode test.
#
# Flow:
#   1. Build gateway:local from docker/services/gateway.dockerfile
#   2. Start MariaDB + Redis (healthchecked) and the gateway (SERVER_ENV=prod)
#      with custom DATABASE_URL / REDIS_URL, upstream URLs on *.avernet.com
#   3. Wait for the gateway /health endpoint and report success / failure
#
# Usage:
#   src/gateway/docker-test/test-gateway.sh            # build + up + health check (detached)
#   src/gateway/docker-test/test-gateway.sh up         # same as above
#   src/gateway/docker-test/test-gateway.sh build      # only rebuild gateway:local
#   src/gateway/docker-test/test-gateway.sh down       # tear down the test stack
#   src/gateway/docker-test/test-gateway.sh status     # show compose ps (and curl /health)
#
# The runtime stack is driven by docker-compose.gateway-test.yml (single source
# of truth for env wiring). Every value can be overridden via env:
#   DATABASE_URL, REDIS_URL, GATEWAY_PORT, *_SERVER_URL, HOST_PORT,
#   principal_signing_key.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd "${SCRIPT_DIR}/../../../docker" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.gateway-test.yml"
PROJECT="gatewaytest"

# Defaults (override via env).
export DATABASE_URL="${DATABASE_URL:-mysql+aiomysql://gateway:gatewaypass@mariadb:3306/gateway_test?charset=utf8mb4}"
export REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"
export GATEWAY_PORT="${GATEWAY_PORT:-8888}"
export BACKEND_SERVER_URL="${BACKEND_SERVER_URL:-https://backend.avernet.com}"
export BAAS_SERVER_URL="${BAAS_SERVER_URL:-https://baas.avernet.com}"
export BCS_SERVER_URL="${BCS_SERVER_URL:-https://bcs.avernet.com}"
export ENGINE_PROXY_SERVER_URL="${ENGINE_PROXY_SERVER_URL:-https://engineproxy.avernet.com}"
export BCSFUSE_SERVER_URL="${BCSFUSE_SERVER_URL:-https://bcsfuse.avernet.com}"
export principal_signing_key="${principal_signing_key:-gateway-test-signing-key-not-for-prod}"

# Host port mapped to the gateway (for the /health probe).
HOST_PORT="${HOST_PORT:-18888}"

log() { printf "\n==> %s\n" "$*"; }

build_image() {
  log "building gateway:local"
  "${DOCKER_DIR}/build-image.sh" services/gateway.dockerfile --image gateway --tag local
}

up_stack() {
  log "starting MariaDB + Redis + gateway (project: ${PROJECT})"
  docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" up -d
}

wait_healthy() {
  local url="http://127.0.0.1:${HOST_PORT}/health"
  local max="${HEALTH_WAIT_SECS:-60}"
  local i=0
  log "waiting for gateway /health at ${url} (up to ${max}s)"
  until curl -fsS "${url}" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "${i}" -ge "${max}" ]; then
      echo "error: gateway did not become healthy within ${max}s" >&2
      docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" ps
      echo "--- gateway logs ---" >&2
      docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" logs gateway --tail 40 >&2 || true
      return 1
    fi
    sleep 1
  done
  printf "\n==> gateway healthy: "
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

