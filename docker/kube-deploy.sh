#!/usr/bin/env bash
# ============================================================
# kube-deploy.sh — Render and deploy a service to Kubernetes
#
# Usage:
#   docker/kube-deploy.sh \
#       --service baas \
#       --image registry/baas:latest \
#       --env REDIS_URL=redis://redis:6379/0 \
#       --env-file baas.env \
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
# --env-file format (one KEY=VALUE per line, # for comments):
#   # baas.env
#   REDIS_URL=redis://redis:6379/0
#   DB_URL=postgres://user:pass@db:5432/baas
#
# Examples:
#   # Dry-run (just print the rendered YAML)
#   docker/kube-deploy.sh --service baas --image baas:local
#
#   # Deploy with inline env vars
#   docker/kube-deploy.sh --service baas \
#       --image registry/baas:v1 \
#       --env REDIS_URL=redis://redis:6379/0 \
#       --apply
#
#   # Deploy with env file
#   docker/kube-deploy.sh --service baas \
#       --image registry/baas:v1 \
#       --env-file baas.env --apply
#
#   # Custom namespace
#   docker/kube-deploy.sh --service gateway \
#       --image registry/gateway:v1 \
#       --namespace staging --apply
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${SCRIPT_DIR}/services/service-deployment.yaml"

# --- Default values ---

SERVICE=""
IMAGE=""
NAMESPACE="avernet"
APPLY=0
ENV_VARS=()
ENV_FILE=""

# Service-specific defaults
declare -A DEFAULT_PORT=(
    [baas]=8888
    [gateway]=8080
    [bcs]=21000
    [backend]=8090
)

# Assuming a 4-core 8GB node, assign roughly:
#   baas/bcs:   1 CPU / 3Gi request, 3 CPU / 6Gi limit (main workloads)
#   gateway/backend: 0.5 CPU / 1Gi request, 1 CPU / 2Gi limit (lighter)
declare -A DEFAULT_CPU_REQ=(
    [baas]="1000m"
    [gateway]="500m"
    [bcs]="1000m"
    [backend]="500m"
)

declare -A DEFAULT_CPU_LIMIT=(
    [baas]="3000m"
    [gateway]="1000m"
    [bcs]="3000m"
    [backend]="1000m"
)

declare -A DEFAULT_MEM_REQ=(
    [baas]="3Gi"
    [gateway]="1Gi"
    [bcs]="3Gi"
    [backend]="1Gi"
)

declare -A DEFAULT_MEM_LIMIT=(
    [baas]="6Gi"
    [gateway]="2Gi"
    [bcs]="6Gi"
    [backend]="2Gi"
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
        --env-file)
            [[ $# -ge 2 ]] || { echo "error: --env-file requires a file path" >&2; exit 2; }
            ENV_FILE="$2"; shift 2 ;;
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

# --- Load env vars from file (if --env-file given) ---

if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_FILE" ]]; then
        echo "error: env file not found: $ENV_FILE" >&2
        exit 2
    fi
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip empty lines and comments
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        # Trim leading/trailing whitespace
        line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        ENV_VARS+=("$line")
    done < "$ENV_FILE"
    echo "==> Loaded ${#ENV_VARS[@]} env vars from $ENV_FILE" >&2
fi

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

RENDERED="$(cat "$TEMPLATE" | envsubst)"

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
        echo "    env:   ${#ENV_VARS[@]} vars"
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