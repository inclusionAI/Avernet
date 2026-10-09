#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

command="${1:-run}"
case "$command" in
    run)
        shift || true
        exec "${SCRIPT_DIR}/singlebox.sh" --standalone "$@"
        ;;
    check|doctor)
        shift
        exec "${SCRIPT_DIR}/singlebox.sh" --standalone check all "$@"
        ;;
    build)
        shift
        exec "${SCRIPT_DIR}/singlebox.sh" --standalone setup all "$@"
        ;;
    start)
        shift
        exec "${SCRIPT_DIR}/singlebox.sh" --standalone start all "$@"
        ;;
    status)
        shift
        exec "${SCRIPT_DIR}/singlebox.sh" --standalone status all "$@"
        ;;
    stop)
        shift
        exec "${SCRIPT_DIR}/singlebox.sh" --standalone stop all "$@"
        ;;
    clean)
        shift
        exec "${SCRIPT_DIR}/singlebox.sh" --standalone clean all "$@"
        ;;
    -h|--help|help)
        exec "${SCRIPT_DIR}/singlebox.sh" --help
        ;;
    *)
        exec "${SCRIPT_DIR}/singlebox.sh" --standalone "$@"
        ;;
esac
