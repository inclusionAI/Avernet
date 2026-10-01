#!/usr/bin/env bash
set -euo pipefail

# Check-only format/lint gate for the gateway module.
#
# Runs `ruff check .` and `ruff format --check .` (non-fixing) so that format
# and lint drift fail the gate with a non-zero exit. Unlike `check-basic`
# (which runs ruff in `--fix` mode for local developer convenience), this gate
# never mutates files.

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
gateway_dir="$(cd "$script_dir/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "gateway format gate failed: uv not found" >&2
  exit 127
fi

cd "$gateway_dir"

echo "[CHECK] check-format-ci: ruff check . && ruff format --check ."

uv run ruff check .
uv run ruff format --check .

echo "gateway format gate passed"