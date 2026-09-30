#!/usr/bin/env bash
# scripts/k8s-tests/avernet/deploy-avernet.sh — deploy the full avernet demo
# stack to a single Kubernetes namespace so the services can reach each other.
#
# Deploys, in one namespace:
#   - shared MariaDB + Redis (shared-infra.yaml), exposed as `mariadb` / `redis`
#   - baas, gateway, and proxy, each via docker/kube-deploy.sh with the
#     avernet-specific env files (in-cluster Service DNS for cross-service
#     links: gateway→baas/proxy, proxy→baas, baas→proxy)
#
# Subcommands:
#   up      build all images, apply shared infra, deploy services, wait healthy
#   build   build the three service images only
#   infra   apply/refresh the shared MariaDB + Redis only
#   services deploy the three app services only (assumes infra is up)
#   down    delete the three app services and the shared infra
#   status  show resources in the namespace
#   (default: up)
#
# Prerequisites: kubectl reaching a cluster, namespace default `avernet`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_TESTS_DIR="${SCRIPT_DIR}/.."
# shellcheck source=../k8s-test-lib.sh
source "${K8S_TESTS_DIR}/k8s-test-lib.sh"

NAMESPACE="${NAMESPACE:-avernet}"
INFRA_FILE="${SCRIPT_DIR}/shared-infra.yaml"
DEPLOY="${K8S_TEST_DEPLOY}"

SERVICES=(
  "baas:services/baas.dockerfile:baas.env"
  "gateway:services/gateway.dockerfile:gateway.env"
  "proxy:services/proxy.dockerfile:proxy.env"
)

build_all() {
  local svc dockerfile
  for entry in "${SERVICES[@]}"; do
    svc="${entry%%:*}"
    dockerfile="$(echo "${entry#*:}" | awk -F: '{print $1}')"
    build_image "${svc}" "${dockerfile}"
  done
}

apply_infra() {
  log "applying shared MariaDB + Redis (namespace ${NAMESPACE})"
  kubectl get namespace "${NAMESPACE}" &>/dev/null || kubectl create namespace "${NAMESPACE}"
  kubectl -n "${NAMESPACE}" apply -f "${INFRA_FILE}"
  log "waiting for mariadb + redis to be ready"
  kubectl -n "${NAMESPACE}" rollout status deployment/mariadb --timeout=120s
  kubectl -n "${NAMESPACE}" rollout status deployment/redis --timeout=120s
}

deploy_services() {
  local svc dockerfile envfile img args
  export_resources
  for entry in "${SERVICES[@]}"; do
    svc="${entry%%:*}"
    rest="${entry#*:}"
    dockerfile="${rest%%:*}"
    envfile="${rest#*:}"
    img="${IMAGE_PREFIX:-}${svc}:${IMAGE_TAG:-local}"
    args=(--service "${svc}" --image "${img}" --namespace "${NAMESPACE}"
          --env-file "${SCRIPT_DIR}/${envfile}")
    log "deploying ${svc} (image ${img}, env ${envfile})"
    PORT="${PORT:-8888}" "${DEPLOY}" "${args[@]}" --apply
  done
}

wait_all_healthy() {
  local svc
  for entry in "${SERVICES[@]}"; do
    svc="${entry%%:*}"
    NAMESPACE="${NAMESPACE}" PORT="${PORT:-8888}" wait_healthy "${svc}"
  done
}

teardown() {
  local svc
  log "deleting app services"
  for entry in "${SERVICES[@]}"; do
    svc="${entry%%:*}"
    kubectl -n "${NAMESPACE}" delete deployment "${svc}" --ignore-not-found
    kubectl -n "${NAMESPACE}" delete service "${svc}" --ignore-not-found
  done
  log "deleting shared infra"
  kubectl -n "${NAMESPACE}" delete -f "${INFRA_FILE}" --ignore-not-found
}

status_cmd() {
  log "resources in namespace ${NAMESPACE}"
  kubectl -n "${NAMESPACE}" get all || true
}

sub="${1:-up}"
case "${sub}" in
  build)    build_all ;;
  infra)    require_kubectl; apply_infra ;;
  services) require_kubectl; deploy_services ;;
  up)       build_all
            require_kubectl
            apply_infra
            deploy_services
            wait_all_healthy ;;
  down)     require_kubectl; teardown ;;
  status)   require_kubectl; status_cmd ;;
  *)
    echo "usage: $0 {build|infra|services|up|down|status}" >&2
    exit 2 ;;
esac