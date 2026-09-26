#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root/apps/backend"
export PYTHONPATH="$repo_root/apps/backend:$repo_root/apps/backend/src:$repo_root/src/engine/src${PYTHONPATH:+:$PYTHONPATH}"
uv run --no-sync --with-editable "$repo_root/src/engine" pytest \
  -c pytest.ini "$repo_root/singlebox/ci/test_claude_code_file_lifecycle.py" -q
