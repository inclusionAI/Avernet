#!/usr/bin/env bash
# Keep this entry point and its agency_*.py helpers together.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${AGENCY_PYTHON:-}" ]]; then
    exec "$AGENCY_PYTHON" "$SCRIPT_DIR/agency_launcher.py" "$@"
fi
if command -v uv >/dev/null 2>&1; then
    exec uv run --script "$SCRIPT_DIR/agency_launcher.py" "$@"
fi
if python3 -c 'import sys, yaml; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
    exec python3 "$SCRIPT_DIR/agency_launcher.py" "$@"
fi
printf '%s\n' 'Install uv, or Python 3.11+ with PyYAML 6.0.3, then rerun.' >&2
exit 1
