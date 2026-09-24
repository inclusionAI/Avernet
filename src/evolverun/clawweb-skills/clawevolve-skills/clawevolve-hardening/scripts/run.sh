#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAWEVOLVE_INVOCATION_CWD="${CLAWEVOLVE_INVOCATION_CWD:-$(pwd)}"
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
      continue
    fi
    candidate_path="$(command -v "$candidate")"
    PY_OK="$($candidate_path - <<'PYV'
import sys
print(1 if sys.version_info >= (3, 10) else 0)
PYV
)"
    if [[ "$PY_OK" == "1" ]]; then
      PYTHON_BIN="$candidate_path"
      break
    fi
  done
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "clawevolve-hardening requires Python >= 3.10. Set PYTHON_BIN to python3.10+ or install python3.12." >&2
  exit 2
fi
exec "$PYTHON_BIN" "$SCRIPT_DIR/run.py" "$@"
