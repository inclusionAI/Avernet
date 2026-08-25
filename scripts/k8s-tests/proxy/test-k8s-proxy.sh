#!/usr/bin/env bash
# scripts/k8s-tests/proxy/test-k8s-proxy.sh — build the proxy image and demo its
# deployment to Kubernetes.
#
# Flow (reusing docker/ shared tooling):
#   1. Build proxy:local from docker/services/proxy.dockerfile
#      (docker/build-image.sh)
#   2. Deploy to Kubernetes via docker/kube-deploy.sh (renders + applies
#      docker/services/service-deployment.yaml)
#   3. Wait for the deployment rollout, port-forward the ClusterIP service,
#      and probe /health
#
# Usage:
#   scripts/k8s-tests/proxy/test-k8s-proxy.sh            # build + deploy + health check
#   scripts/k8s-tests/proxy/test-k8s-proxy.sh up         # same as above
#   scripts/k8s-tests/proxy/test-k8s-proxy.sh build      # only rebuild proxy:local
#   scripts/k8s-tests/proxy/test-k8s-proxy.sh down       # delete deployment + service
#   scripts/k8s-tests/proxy/test-k8s-proxy.sh status     # show k8s resources
#
# Prerequisites:
#   - kubectl on PATH, authenticated to a reachable cluster
#   - the proxy:local image available in the cluster's pull context
#     (for remote clusters, build+push first, then set IMAGE accordingly)
#
# Configuration (all overridable via environment):
#   NAMESPACE   (default avernet), PORT (default 8888), IMAGE (default proxy),
#   IMAGE_TAG (default local), ENV_FILE (default proxy.env next to this script),
#   HOST_PORT (port-forward local port, default 18080), HEALTH_WAIT_SECS.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/.."

# shellcheck source=../k8s-test-lib.sh
source "${LIB_DIR}/k8s-test-lib.sh"

export NAMESPACE="${NAMESPACE:-avernet}"
export PORT="${PORT:-8888}"
export ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/proxy.env}"

run_k8s_test "proxy" "services/proxy.dockerfile" "${1:-up}"