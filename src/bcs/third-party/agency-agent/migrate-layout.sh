#!/usr/bin/env bash
# Offline migration only; never launches an engine or calls BCS.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${AGENCY_PYTHON:-python3}" "$SCRIPT_DIR/agency_migrate.py" "$@"
