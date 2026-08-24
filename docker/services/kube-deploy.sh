#!/usr/bin/env bash
# ============================================================
# kube-deploy.sh — Render and deploy a service to Kubernetes
#
# Usage:
#   docker/services/kube-deploy.sh \
#       --service baas \
#       --image registry/baas:latest \
#       --env REDIS_URL=redis://redis:6379/0 \
#       --env DB_URL=sqlite:////tmp/baas.db \
#       [--namespace avernet] \
#       [--apply]                 # apply with kubectl (default: dry-run print)
#
# Supported services (with default port):
#   baas       — SecBaaS platform service (port 8888)
#   gateway    — API gateway (port 8080)
#   bcs        — BCS coordination service (port 21000)
#   backend    — Backend service (port 8090)
#
# The script renders docker/services/service-deployment.yaml via
# envsubst, then optionally applies it with kubectl.
#
# Examples:
#   # Dry-run (just print the rendered YAML)
#   docker/services/kube-deploy.sh --service baas --image baas:local
#
#   # Deploy with env vars
#   docker/services/kube-deploy.sh --service baas \
#       --image registry/baas:v1 \
#       --env REDIS_URL=redis://redis:6379/0 \
#       --apply
#
#   # Custom namespace
#   docker/services/kube-deploy.sh --service gateway \
#       --image registry/gateway:v1 \
#       --namespace staging --apply
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${SCRIPT_DIR}/service-deployment.yaml"

# --- Default values ---

SERVICE=""
IMAGE=""
NAMESPACE="avernet"
APPLY=0
ENV_VARS=()

# Service-specific defaults
declare -A DEFAULT_PORT=(
    [baas]=8888
    [gateway]=8080
    [bcs]=21000
    [backend]=8090
)

declare -A DEFAULT_CPU_REQ=(
    [baas]="500m"
    [gateway]="250m"
    [bcs]="500m"
    [backend]="250m"
)

declare -A DEFAULT_CPU_LIMIT=(
    [baas]="2000m"
    [gateway]="1000m"
    [bcs]="2000m"
    [backend]="1000m"
)

declare -A DEFAULT_MEM_REQ=(
    [baas]="512Mi"
    [gateway]="256Mi"
    [bcs]="512Mi"
    [backend]="256Mi"
)

declare -A DEFAULT_MEM_LIMIT=(
    [baas]="2Gi"
    [gateway]="1Gi"
    [bcs]="2Gi"
    [backend]="1Gi"
)

# --- Parse arguments ---

usage() {
    sed -n '2,/^# ====/p; /^# ====/q' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --service)
            [[ $# -ge 2 ]] || { echo "error: --service requires an argument" >&2; exit 2; }
            SERVICE="$2"; shift 2 ;;
        --image)
            [[ $# -ge 2 ]] || { echo "error: --image requires an argument" >&2; exit 2; }
            IMAGE="$2"; shift 2 ;;
        --namespace|-n)
            [[ $# -ge 2 ]] || { echo "error: --namespace requires an argument" >&2; exit 2; }
            NAMESPACE="$2"; shift 2 ;;
        --env)
            [[ $# -ge 2 ]] || { echo "error: --env requires key=value" >&2; exit 2; }
            ENV_VARS+=("$2"); shift 2 ;;
        --apply)
            APPLY=1; shift ;;
        --help|-h)
            usage ;;
        *)
            echo "error: unknown option $1" >&2; exit 2 ;;
    esac
done

# --- Validate ---

if [[ -z "$SERVICE" ]]; then
    echo "error: --service is required (baas|gateway|bcs|backend)" >&2
    exit 2
fi
if [[ -z "$IMAGE" ]]; then
    echo "error: --image is required" >&2
    exit 2
fi
if [[ -z "${DEFAULT_PORT[$SERVICE]:-}" ]]; then
    echo "error: unknown service '$SERVICE'" >&2
    echo "  supported: baas, gateway, bcs, backend" >&2
    exit 2
fi

PORT="${PORT:-${DEFAULT_PORT[$SERVICE]}}"
CPU_REQUEST="${DEFAULT_CPU_REQ[$SERVICE]}"
CPU_LIMIT="${DEFAULT_CPU_LIMIT[$SERVICE]}"
MEMORY_REQUEST="${DEFAULT_MEM_REQ[$SERVICE]}"
MEMORY_LIMIT="${DEFAULT_MEM_LIMIT[$SERVICE]}"

# --- Render env vars into YAML ---

ENV_YAML=""
if [[ ${#ENV_VARS[@]} -gt 0 ]]; then
    ENV_YAML="env:"
    for entry in "${ENV_VARS[@]}"; do
        key="${entry%%=*}"
        val="${entry#*=}"
        ENV_YAML="${ENV_YAML}\n        - name: ${key}\n          value: \"${val}\""
    done
fi

# --- Render template via envsubst ---

export SERVICE NAMESPACE IMAGE PORT CPU_REQUEST CPU_LIMIT MEMORY_REQUEST MEMORY_LIMIT
export ENV_VARS_YAML="$ENV_YAML"

# envsubst doesn't support multi-line variables well, so use sed
RENDERED="$(cat "$TEMPLATE")"
RENDERED="${RENDERED//\$\{SERVICE\>/$SERVICE}"  # not needed, envsubst handles it

# Use envsubst for simple vars, then sed for ENV_VARS block
RENDERED="$(echo "$RENDERED" | envsubst)"
# Replace the ENV_VARS placeholder
if [[ -n "$ENV_YAML" ]]; then
    RENDERED="$(echo "$RENDERED" | sed "s|\${ENV_VARS}|${ENV_YAML//$'\n'/\\n}|g")"
else
    RENDERED="$(echo "$RENDERED" | sed 's|        ${ENV_VARS}||')"
fi

# --- Output or apply ---

if [[ "$APPLY" -eq 1 ]]; then
    echo "==> Deploying $SERVICE to namespace $NAMESPACE"
    echo "    image: $IMAGE"
    echo "    port:  $PORT"
    if [[ ${#ENV_VARS[@]} -gt 0 ]]; then
        echo "    env:   ${ENV_VARS[*]}"
    fi
    echo

    # Create namespace if not exists
    kubectl get namespace "$NAMESPACE" &>/dev/null || \
        kubectl create namespace "$NAMESPACE"

    echo "$RENDERED" | kubectl apply -f -

    echo
    echo "==> Deployed. Check status:"
    echo "    kubectl -n $NAMESPACE get pods -l app=$SERVICE"
else
    echo "$RENDERED"
fi
