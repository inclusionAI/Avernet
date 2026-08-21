#!/usr/bin/env bash
# src/baas/docker-test/test-baas.sh — build the baas image and run a full prod-mode test.
#
# Flow:
#   1. Build baas:local from docker/services/baas.dockerfile
#   2. Start MariaDB + Redis (healthchecked) and baas (SERVER_ENV=prod)
#      with custom DATABASE_URL / REDIS_URL plus the ACK placeholders that the
#      prod overlay declares under strict env expansion
#   3. Wait for the baas /health endpoint and report success / failure
#
# Usage:
#   src/baas/docker-test/test-baas.sh            # build + up + health check (detached)
#   src/baas/docker-test/test-baas.sh up         # same as above
#   src/baas/docker-test/test-baas.sh build      # only rebuild baas:local
#   src/baas/docker-test/test-baas.sh down       # tear down the test stack
#   src/baas/docker-test/test-baas.sh status     # show compose ps (and curl /health)
#
# The runtime stack is driven by docker-compose.baas-test.yml (single source
# of truth for env wiring). Every value can be overridden via env:
#   DATABASE_URL, REDIS_URL, BAAS_PORT, ACK_SERVER, ACK_TOKEN, DEFAULT_IMAGE,
#   DEPLOY_ENV, HOST_PORT.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd "${SCRIPT_DIR}/../../../docker" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.baas-test.yml"
PROJECT="baastest"

# Defaults (override via env).
export DATABASE_URL="${DATABASE_URL:-mysql+aiomysql://baas:baaspass@mariadb:3306/baas_test?charset=utf8mb4}"
export REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"
export BAAS_PORT="${BAAS_PORT:-8888}"
# prod overlay declares these under strict env expansion; inject harmless
# test values so config loading succeeds. The aliyun_ack sandbox is a lazy
# factory and is not contacted during boot, after /health this is enough.
export ACK_SERVER="${ACK_SERVER:-https://ack-test.example.com}"
export ACK_TOKEN="${ACK_TOKEN:-ack-test-token}"
export DEFAULT_IMAGE="${DEFAULT_IMAGE:-openclaw:latest}"
export DEPLOY_ENV="${DEPLOY_ENV:-ALI_YUN_ACK}"

# Host port mapped to baas (for the /health probe).
HOST_PORT="${HOST_PORT:-18889}"

log() { printf "\n==> %s\n" "$*"; }

build_image() {
  log "building baas:local"
  "${DOCKER_DIR}/build-image.sh" services/baas.dockerfile --image baas --tag local
}

up_stack() {
  log "starting MariaDB + Redis + baas (project: ${PROJECT})"
  docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" up -d
}

wait_healthy() {
  local url="http://127.0.0.1:${HOST_PORT}/health"
  local max="${HEALTH_WAIT_SECS:-90}"
  local i=0
  log "waiting for baas /health at ${url} (up to ${max}s)"
  until curl -fsS "${url}" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "${i}" -ge "${max}" ]; then
      echo "error: baas did not become healthy within ${max}s" >&2
      docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" ps
      echo "--- baas logs ---" >&2
      docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" logs baas --tail 40 >&2 || true
      return 1
    fi
    sleep 1
  done
  printf "\n==> baas healthy: "
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

