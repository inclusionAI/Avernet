#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAWEVOLVE_INVOCATION_CWD="${CLAWEVOLVE_INVOCATION_CWD:-$(pwd)}"
if command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.12)"
else
  PYTHON_BIN="$(command -v python3)"
fi

PY_OK="$($PYTHON_BIN - <<'PY'
import sys
print(1 if sys.version_info >= (3, 10) else 0)
PY
)"
if [[ "$PY_OK" != "1" ]]; then
  echo "clawevolve-diagnose requires Python >= 3.10 because it uses modern type syntax." >&2
  echo "Install python3.12 or ensure python3 is 3.10+." >&2
  exit 2
fi
exec "$PYTHON_BIN" "$SCRIPT_DIR/run.py" "$@"
