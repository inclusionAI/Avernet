#!/usr/bin/env bash
set -euo pipefail

printf '%s:%s\n' "$1" "${SERVER_ENV:-}" >> "${CAPTURE_FILE}"
