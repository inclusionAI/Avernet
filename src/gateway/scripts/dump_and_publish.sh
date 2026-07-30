#!/bin/bash
# Dump OpenAPI descriptions from upstream services and publish them to the
# gateway's schema catalog, gating each against backward-incompatible changes.
#
# Usage:
#   dump_and_publish.sh                         # dump + gate + publish all
#   dump_and_publish.sh --skip backend          # skip just backend
#   dump_and_publish.sh --allow-breaking baas   # allow breaking on baas
#   dump_and_publish.sh --dry-run               # dump only, no publish
#
# Required env vars (per upstream):
#   BACKEND_URL   — backend service URL (for health check)
#   BAAS_URL   — BaaS service URL (for health check)
#
# The script runs from the gateway directory and assumes sibling src/ dirs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATEWAY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE_DIR="$(cd "$GATEWAY_DIR/../.." && pwd)"
SCHEMAS_DIR="$GATEWAY_DIR/configs/schemas"
TMPDIR="${TMPDIR:-/tmp}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${CYAN}=== $* ===${NC}"; }

# ── CLI ────────────────────────────────────────────────────────────────────────
SKIP=()
ALLOW_BREAKING=()
DRY_RUN=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip)       SKIP+=("$2"); shift 2 ;;
        --allow-breaking) ALLOW_BREAKING+=("$2"); shift 2 ;;
        --dry-run)    DRY_RUN=true; shift ;;
        *)            log_error "Unknown arg: $1"; exit 1 ;;
    esac
done

_should_run() {
    local name="$1"
    for s in "${SKIP[@]:-}"; do [[ "$s" == "$name" ]] && return 1; done
    return 0
}

_allow_breaking() {
    local name="$1"
    for b in "${ALLOW_BREAKING[@]:-}"; do [[ "$b" == "$name" ]] && return 0; done
    return 1
}

mkdir -p "$SCHEMAS_DIR"

# ── upstream registry ──────────────────────────────────────────────────────────
_upstream_dir() {
    case "$1" in
        backend) echo "src/backend" ;;
        baas)    echo "src/baas" ;;
        *)       echo "" ;;
    esac
}

_upstream_env() {
    case "$1" in
        backend) echo "DEPLOY_PROFILE=community" ;;
        baas)    echo "SECBAAS_RUN_MODE=bare" ;;
        *)       echo "" ;;
    esac
}

# ── dump helpers ───────────────────────────────────────────────────────────────
_dump_upstream() {
    local name="$1"
    local dir="$WORKSPACE_DIR/$(_upstream_dir "$name")"
    local env_vars="$(_upstream_env "$name")"
    shift
    local extra_args=("${@:-}")

    _should_run "$name" || { log_warn "Skipping $name (--skip)"; return 0; }

    log_step "Dumping $name"
    (
        cd "$dir"
        if [[ -n "$env_vars" ]]; then
            export ${env_vars//,/ }
        fi
        uv run python "scripts/dump_openapi.py" "$TMPDIR/${name}.openapi.json" "${extra_args[@]:-}"
    )
    log_info "$name dumped → $TMPDIR/${name}.openapi.json"
}

_gate_and_publish() {
    local name="$1" artifact="$2" candidate="$3"

    _should_run "$name" || return 0

    log_step "Gating $name"
    local gate_args=("$artifact" "$candidate")
    if _allow_breaking "$name"; then
        gate_args+=(--allow-breaking)
        log_warn "$name: allowing breaking changes"
    fi

    cd "$GATEWAY_DIR" && uv run python "$SCRIPT_DIR/gate_and_publish_openapi.py" "${gate_args[@]}"
    log_info "$name published → $artifact"
}

# ── main ───────────────────────────────────────────────────────────────────────
main() {
    log_info "Gateway dir:  $GATEWAY_DIR"
    log_info "Workspace:     $WORKSPACE_DIR"
    log_info "Schemas dir:   $SCHEMAS_DIR"
    if $DRY_RUN; then
        log_warn "DRY RUN — dumps only, no publish"
    fi

    # ── backend ────────────────────────────────────────────────────────────────
    _dump_upstream backend
    if ! $DRY_RUN; then
        _gate_and_publish \
            backend \
            "$SCHEMAS_DIR/bots.openapi.json" \
            "$TMPDIR/backend.openapi.json"
    fi

    _dump_upstream baas
    if ! $DRY_RUN; then
        _gate_and_publish \
            baas \
            "$SCHEMAS_DIR/baas.openapi.json" \
            "$TMPDIR/baas.openapi.json"
    fi

    log_step "Done"
    log_info "Published artifacts in $SCHEMAS_DIR"
    ls -la "$SCHEMAS_DIR"/*.json 2>/dev/null || log_warn "No artifacts found"
}

main "$@"