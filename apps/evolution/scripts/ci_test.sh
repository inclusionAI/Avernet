#!/usr/bin/env bash
# Standalone Evolution + shared Genome contracts; never add Backend to PYTHONPATH.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
mode="${1:---all}"
case "$mode" in --lint|--test|--all) ;; *) echo 'usage: ci_test.sh [--lint|--test|--all]' >&2; exit 2;; esac
cd "$root"
uv sync --project apps/evolution --frozen
if [[ "$mode" != --test ]]; then
  uv run --project apps/evolution --frozen ruff check apps/evolution packages/genome-contracts
  uv run --project apps/evolution --frozen ruff format --check apps/evolution packages/genome-contracts
fi
if [[ "$mode" != --lint ]]; then
  env -u PYTHONPATH uv run --project apps/evolution --frozen pytest apps/evolution/tests -q
fi
