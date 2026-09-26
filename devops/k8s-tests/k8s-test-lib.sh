#!/usr/bin/env bash
# scripts/k8s-tests/k8s-test-lib.sh — shared helpers for k8s deployment demo tests.
#
# This is NOT meant to be executed directly. It is sourced by the per-service
# scripts under scripts/k8s-tests/<service>/test-k8s-<service>.sh and exposes a
# single parameterized entrypoint `run_k8s_test` plus supporting functions.
#
# Reused shared tooling (do not re-implement these):
#   docker/build-image.sh  — builds <service>:local from a dockerfile
#   docker/kube-deploy.sh  — renders + applies service-deployment.yaml
#
# Configuration (all overridable via environment):
#   NAMESPACE   — Kubernetes namespace (default: avernet)
#   PORT        — container listen port (default: 8888, matching the
#                 dockerfiles; overrides kube-deploy.sh's stale 80 default)
#   IMAGE       — image reference (default: <service>:local)
#   IMAGE_TAG   — image tag (default: local)
#   ENV_FILE    — path to a KEY=VALUE env file passed via --env-file (optional)
#   HOST_PORT   — local port for the port-forward health probe (default: 18080)
#   HEALTH_WAIT_SECS — max seconds to wait for the deployment to roll out
#                      and /health to respond (default: 120)

set -euo pipefail

# Locate repo root relative to this file (scripts/k8s-tests/k8s-test-lib.sh).
K8S_TEST_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export K8S_TEST_REPO_ROOT="$(cd "${K8S_TEST_LIB_DIR}/../.." && pwd)"
export K8S_TEST_BUILD_IMAGE="${K8S_TEST_REPO_ROOT}/docker/build-image.sh"
export K8S_TEST_DEPLOY="${K8S_TEST_REPO_ROOT}/docker/kube-deploy.sh"

log() { printf "\n==> %s\n" "$*"; }

# Require kubectl, failing clearly when a cluster is unreachable.
require_kubectl() {
  command -v kubectl >/dev/null 2>&1 || {
    echo "error: kubectl not found on PATH" >&2
    exit 1
  }
  if ! kubectl cluster-info >/dev/null 2>&1; then
    echo "error: kubectl cannot reach a cluster; configure kubeconfig first" >&2
    exit 1
  fi
}

# Build the service image via docker/build-image.sh.
build_image() {
  local service="$1"
  local dockerfile="$2"   # e.g. services/baas.dockerfile
  local image="${IMAGE:-${service}}"
  local tag="${IMAGE_TAG:-local}"
  log "building ${image}:${tag} from ${dockerfile}"
  "${K8S_TEST_BUILD_IMAGE}" "${dockerfile}" --image "${image}" --tag "${tag}"
}

# Export the k8s resource spec for the deploy. Defaults to a small demo
# footprint (1 replica, modest CPU/memory) so the stack schedules on a local
# single-node cluster; each is overridable via the same-named env var.
#   REPLICAS, CPU_REQUEST, CPU_LIMIT, MEMORY_REQUEST, MEMORY_LIMIT
export_resources() {
  export REPLICAS="${REPLICAS:-1}"
  export CPU_REQUEST="${CPU_REQUEST:-250m}"
  export CPU_LIMIT="${CPU_LIMIT:-500m}"
  export MEMORY_REQUEST="${MEMORY_REQUEST:-256Mi}"
  export MEMORY_LIMIT="${MEMORY_LIMIT:-512Mi}"
}

# Deploy the service via docker/kube-deploy.sh.
deploy() {
  local service="$1"
  local namespace="${NAMESPACE:-avernet}"
  local port="${PORT:-8888}"
  local image="${IMAGE:-${service}}:${IMAGE_TAG:-local}"
  local args=(--service "${service}" --image "${image}" --namespace "${namespace}")
  if [[ -n "${ENV_FILE:-}" ]]; then
    [[ -f "${ENV_FILE}" ]] || { echo "error: env file not found: ${ENV_FILE}" >&2; exit 2; }
    args+=(--env-file "${ENV_FILE}")
  fi
  log "deploying ${service} to namespace ${namespace} (image ${image}, port ${port})"
  export_resources
  PORT="${port}" "${K8S_TEST_DEPLOY}" "${args[@]}" --apply
}

# Wait for the deployment to become available, then probe /health via a
# background kubectl port-forward (the service is ClusterIP).
wait_healthy() {
  local service="$1"
  local namespace="${NAMESPACE:-avernet}"
  local port="${PORT:-8888}"
  local host_port="${HOST_PORT:-18080}"
  local max="${HEALTH_WAIT_SECS:-120}"

  log "waiting for rollout of deployment/${service} (up to ${max}s)"
  if ! kubectl -n "${namespace}" rollout status "deployment/${service}" \
      --timeout="${max}s" >/dev/null 2>&1; then
    echo "error: deployment/${service} did not become ready" >&2
    kubectl -n "${namespace}" get pods -l "app=${service}" >&2
    return 1
  fi

  log "port-forwarding svc/${service}:${port} -> 127.0.0.1:${host_port}"
  kubectl -n "${namespace}" port-forward "svc/${service}" "${host_port}:${port}" \
      >/dev/null 2>&1 &
  local pf_pid=$!
  trap 'kill "${pf_pid:-}" 2>/dev/null || true' RETURN

  local url="http://127.0.0.1:${host_port}/health"
  local i=0
  log "waiting for /health at ${url}"
  until curl -fsS "${url}" >/dev/null 2>&1; do
    i=$((i + 1))
    if [[ "${i}" -ge "${max}" ]]; then
      echo "error: ${service} /health did not respond within ${max}s" >&2
      kubectl -n "${namespace}" logs "deployment/${service}" --tail 40 >&2 || true
      return 1
    fi
    sleep 1
  done
  printf "\n==> %s healthy: " "${service}"
  curl -fsS "${url}"
  printf "\n"
}

# Tear down the deployment + service resources.
teardown() {
  local service="$1"
  local namespace="${NAMESPACE:-avernet}"
  local manifest
  log "deleting deployment, service, and namespace-owned resources for ${service}"
  kubectl -n "${namespace}" delete deployment "${service}" --ignore-not-found
  kubectl -n "${namespace}" delete service "${service}" --ignore-not-found
}

# Report k8s resource state and current /health result.
status() {
  local service="$1"
  local namespace="${NAMESPACE:-avernet}"
  log "resources for ${service} in namespace ${namespace}"
  kubectl -n "${namespace}" get all -l "app=${service}" || true
}

# Entry point: run_k8s_test <service> <dockerfile> <subcommand>.
#   <subcommand> is one of build|up|down|status (default up).
run_k8s_test() {
  local service="$1"
  local dockerfile="$2"
  local sub="${3:-up}"

  case "${sub}" in
    build)  build_image "${service}" "${dockerfile}" ;;
    up)     build_image "${service}" "${dockerfile}"
            require_kubectl
            deploy "${service}"
            wait_healthy "${service}" ;;
    down)   require_kubectl
            teardown "${service}" ;;
    status) require_kubectl
            status "${service}" ;;
    *)
      echo "usage: $0 {build|up|down|status}" >&2
      exit 2 ;;
  esac
}