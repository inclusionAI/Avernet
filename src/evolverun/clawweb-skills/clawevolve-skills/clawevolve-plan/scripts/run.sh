#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAWEVOLVE_INVOCATION_CWD="${CLAWEVOLVE_INVOCATION_CWD:-$(pwd)}"
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.12)"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi

PY_OK="$($PYTHON_BIN - <<'PYV'
import sys
print(1 if sys.version_info >= (3, 10) else 0)
PYV
)"
if [[ "$PY_OK" != "1" ]]; then
  echo "clawevolve-plan requires Python >= 3.10. Set PYTHON_BIN to python3.10+ or install python3.12." >&2
  exit 2
fi
exec "$PYTHON_BIN" "$SCRIPT_DIR/run.py" "$@"
