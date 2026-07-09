#!/usr/bin/env bash
set -euo pipefail

# Backward-compatible wrapper for the old local_setup.sh entrypoint.
#
# The historical implementation mixed open-source local setup with internal
# development services and private registries. Keep this file as a small public
# compatibility layer and route all supported local workflows to singlebox.sh.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SINGLEBOX="${SCRIPT_DIR}/singlebox.sh"

warn() {
  printf '[WARN] %s\n' "$*" >&2
}

die() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

show_help() {
  cat <<'EOF'
Usage: ./scripts/local_setup.sh [OPTIONS] [COMMAND] [SERVICE|GROUP]

Deprecated compatibility wrapper.

Use ./scripts/singlebox.sh for new local development workflows. This wrapper
only forwards open-source local workflows and intentionally does not support the
old internal dev setup.

Common replacements:
  ./scripts/local_setup.sh check
    -> ./scripts/singlebox.sh check

  ./scripts/local_setup.sh start bcs
    -> ./scripts/singlebox.sh --bcs-env local start bcs

  ./scripts/local_setup.sh start bcs_frontend
    -> ./scripts/singlebox.sh --bcs-env local start bcs_frontend

  ./scripts/local_setup.sh status
    -> ./scripts/singlebox.sh status

Unsupported legacy options:
  --dev, -d, --bcs-env dev

Run ./scripts/singlebox.sh --help for the maintained command reference.
EOF
}

reject_internal_dev_mode() {
  local previous=""
  local arg

  for arg in "$@"; do
    case "$arg" in
      --dev|-d|--bcs-env=dev)
        die "Internal dev mode is not supported by local_setup.sh. Use ./scripts/singlebox.sh --bcs-env local ..."
        ;;
    esac

    if [ "$previous" = "--bcs-env" ] && [ "$arg" = "dev" ]; then
      die "Internal dev mode is not supported by local_setup.sh. Use ./scripts/singlebox.sh --bcs-env local ..."
    fi

    previous="$arg"
  done
}

main() {
  case "${1:-}" in
    -h|--help|help)
      show_help
      return 0
      ;;
  esac

  [ -x "$SINGLEBOX" ] || die "Missing executable: ${SINGLEBOX}"

  reject_internal_dev_mode "$@"

  warn "scripts/local_setup.sh is deprecated; forwarding to scripts/singlebox.sh"
  local args=()
  local arg
  for arg in "$@"; do
    case "$arg" in
      --local|-l)
        warn "Ignoring deprecated local_setup mode flag ${arg}; singlebox uses isolated standalone paths by default."
        ;;
      *)
        args+=("$arg")
        ;;
    esac
  done
  exec "$SINGLEBOX" "${args[@]}"
}

main "$@"
