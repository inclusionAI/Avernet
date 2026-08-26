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
# Supported services (default port, probe path):
#   baas       — SecBaaS platform service   (8888,  /health)
#   gateway    — API gateway                (8888,  /health)
#   proxy      — Sandbox proxy              (8888,  /health)
#   bcs        — BCS coordination service   (21000, /health)
#   backend    — Backend service            (8888,  /api/health)
#
# Each default is the port that service actually listens on out of the box, and
# the path it actually serves. Override the port with PORT=... in the
# environment; see the note on DEFAULT_PORT below for which services can honour
# that inside the container.
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

# Service-specific defaults.
#
# These were all 8080, which no service listens on: baas/gateway/proxy default
# module_config.web.port to 8888, bcs's toml sets 21000, and the backend's
# main.py defaults to 8888. Every rendered manifest therefore published a port
# with no listener behind it. Each entry below is the service's real default.
declare -A DEFAULT_PORT=(
    [baas]=8888
    [gateway]=8888
    [proxy]=8888
    [bcs]=21000
    [backend]=8888
)

# The HTTP path each service answers health probes on. The backend mounts its
# routes under /api and serves /api/health; the rest serve /health at the root.
# A wrong path here is silent and fatal: readiness never turns true and liveness
# restarts an otherwise healthy pod on a loop.
declare -A DEFAULT_PROBE_PATH=(
    [baas]=/health
    [gateway]=/health
    [proxy]=/health
    [bcs]=/health
    [backend]=/api/health
)

# The env var (if any) through which a service lets the environment override the
# port from its config file. gateway, proxy and backend each read one and let it
# win; injecting it below keeps the container's listener on the port this
# manifest publishes, so PORT=... moves both together instead of only
# relabelling the Service. baas and bcs have no such override — their port comes
# from the mounted config / toml alone, so overriding PORT for them means
# editing that config to match.
declare -A PORT_ENV_VAR=(
    [baas]=""
    [gateway]=GATEWAY_PORT
    [proxy]=SANDBOXPROXY_PORT
    [bcs]=""
    [backend]=BACKEND_PORT
)

# All services default to 4 CPU / 8Gi spec, 2 replicas.
DEFAULT_REPLICAS=2
DEFAULT_CPU_REQUEST="4"
DEFAULT_CPU_LIMIT="4"
DEFAULT_MEM_REQUEST="8Gi"
DEFAULT_MEM_LIMIT="8Gi"

# --- Parse arguments ---

usage() {
    # Print the header comment block: everything between the opening and closing
    # banner, with the leading "# " stripped. The previous sed quit on the first
    # /^# ====/ line, which is the opening banner itself, so --help emitted that
    # single line and nothing else. Same shape as docker/build-image.sh.
    awk 'NR>2 { if ($0 ~ /^# ====/) exit; sub(/^# ?/,""); print }' "${BASH_SOURCE[0]}"
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
    echo "error: --service is required (baas|gateway|proxy|bcs|backend)" >&2
    exit 2
fi
if [[ -z "$IMAGE" ]]; then
    echo "error: --image is required" >&2
    exit 2
fi
if [[ -z "${DEFAULT_PORT[$SERVICE]:-}" ]]; then
    echo "error: unknown service '$SERVICE'" >&2
    echo "  supported: baas, gateway, proxy, bcs, backend" >&2
    exit 2
fi

PORT="${PORT:-${DEFAULT_PORT[$SERVICE]}}"
PROBE_PATH="${PROBE_PATH:-${DEFAULT_PROBE_PATH[$SERVICE]}}"
REPLICAS="${REPLICAS:-$DEFAULT_REPLICAS}"
CPU_REQUEST="${CPU_REQUEST:-$DEFAULT_CPU_REQUEST}"
CPU_LIMIT="${CPU_LIMIT:-$DEFAULT_CPU_LIMIT}"
MEMORY_REQUEST="${MEMORY_REQUEST:-$DEFAULT_MEM_REQUEST}"
MEMORY_LIMIT="${MEMORY_LIMIT:-$DEFAULT_MEM_LIMIT}"

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

# --- Always add deploy_time env var ---

ENV_VARS+=("DEPLOY_TIME=$(date '+%Y-%m-%dT%H:%M:%S%z')")

# --- Keep the container's listener on the port this manifest publishes ---

# For a service that reads a port env var, inject it set to PORT. Without this a
# PORT override renamed the Service's target while the process kept listening
# where its config said, which is the same dead port the old uniform 8080
# produced. An explicit --env for the same key wins: the operator asked for it by
# name, so we leave it alone and only warn if it disagrees with what we publish.
PORT_ENV="${PORT_ENV_VAR[$SERVICE]:-}"
if [[ -n "$PORT_ENV" ]]; then
    explicit=""
    for entry in "${ENV_VARS[@]}"; do
        if [[ "${entry%%=*}" == "$PORT_ENV" ]]; then
            explicit="${entry#*=}"
            break
        fi
    done
    if [[ -z "$explicit" ]]; then
        ENV_VARS+=("${PORT_ENV}=${PORT}")
    elif [[ "$explicit" != "$PORT" ]]; then
        echo "warning: --env ${PORT_ENV}=${explicit} disagrees with the published port ${PORT};" >&2
        echo "         the Service and probes will target ${PORT} while the container listens on ${explicit}." >&2
        echo "         Set PORT=${explicit} to move both together." >&2
    fi
elif [[ "${PORT}" != "${DEFAULT_PORT[$SERVICE]}" ]]; then
    echo "warning: ${SERVICE} takes its port from its mounted config, not the environment;" >&2
    echo "         PORT=${PORT} only changes the manifest. Edit that config to match, or the" >&2
    echo "         Service and probes will target a port with no listener." >&2
fi

# --- Render env vars into YAML ---

ENV_YAML=""
if [[ ${#ENV_VARS[@]} -gt 0 ]]; then
    ENV_YAML="env:"
    for entry in "${ENV_VARS[@]}"; do
        key="${entry%%=*}"
        val="${entry#*=}"
        ENV_YAML="${ENV_YAML}
        - name: ${key}
          value: \"${val}\""
    done
fi

# --- Render template ---

# envsubst first for ${...} placeholders (won't touch __ENV_VARS__)
export SERVICE NAMESPACE IMAGE PORT PROBE_PATH REPLICAS CPU_REQUEST CPU_LIMIT MEMORY_REQUEST MEMORY_LIMIT
RENDERED="$(cat "$TEMPLATE" | envsubst)"

# Then replace __ENV_VARS__ with the env block (or remove if empty)
if [[ -n "$ENV_YAML" ]]; then
    RENDERED="${RENDERED//__ENV_VARS__/$ENV_YAML}"
else
    RENDERED="${RENDERED//        __ENV_VARS__/}"
fi
export SERVICE NAMESPACE IMAGE PORT PROBE_PATH REPLICAS CPU_REQUEST CPU_LIMIT MEMORY_REQUEST MEMORY_LIMIT
RENDERED="$(echo "$RENDERED" | envsubst)"

# --- Output or apply ---

if [[ "$APPLY" -eq 1 ]]; then
    echo "==> Deploying $SERVICE to namespace $NAMESPACE"
    echo "    image: $IMAGE"
    echo "    port:  $PORT"
    echo "    probe: $PROBE_PATH"
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