#!/usr/bin/env bash
set -euo pipefail

quiet=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --quiet|-q)
      quiet=1
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "error: not inside a git repository" >&2
  exit 1
fi

if [[ ! -d ".githooks" ]]; then
  echo "error: .githooks directory not found" >&2
  exit 1
fi

hooks_before="$(git config --worktree --get core.hooksPath 2>/dev/null || git config --get core.hooksPath 2>/dev/null || true)"

chmod +x .githooks/pre-push
chmod +x scripts/ci/pre_push.sh
chmod +x scripts/ci/python_sast_local.sh
chmod +x scripts/ci/singlebox_coverage.sh
chmod +x scripts/ci/report_check.py
chmod +x src/backend/scripts/ci_test.sh
chmod +x src/baas/scripts/ci_test.sh
chmod +x src/bcs/scripts/ci_test.sh
chmod +x src/engine/scripts/ci_test.sh
chmod +x src/frontend/scripts/ci_test.sh

config_scope="this worktree"
if ! git config --worktree core.hooksPath .githooks 2>/dev/null; then
  git config core.hooksPath .githooks
  config_scope="this repository"
fi

if [[ "$quiet" -eq 0 || "$hooks_before" != ".githooks" ]]; then
  echo "installed OCB git hooks for ${config_scope}: core.hooksPath=.githooks"
  echo "pre-push will run local CI gates before push"
fi
