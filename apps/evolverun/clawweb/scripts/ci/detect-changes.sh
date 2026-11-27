#!/usr/bin/env bash
set -euo pipefail

# Manual runs always exercise the full gate, even on a non-merge commit.
if [ "${GITHUB_EVENT_NAME:-}" = "workflow_dispatch" ]; then
  echo "skip=false" >> "$GITHUB_OUTPUT"
  exit 0
fi
if [ "${GITHUB_EVENT_NAME:-}" != "pull_request" ]; then
  echo "Expected pull_request or workflow_dispatch event" >&2
  exit 1
fi
if [ -z "${GITHUB_SHA:-}" ]; then
  echo "Missing PR merge SHA" >&2
  exit 1
fi

merge_sha="$(git rev-parse --verify --end-of-options "${GITHUB_SHA}^{commit}")"
parents="$(git show --no-patch --format=%P "$merge_sha" --)"
read -r first_parent second_parent extra_parents <<< "$parents"
if [ -z "$first_parent" ] || [ -z "$second_parent" ] || [ -n "$extra_parents" ]; then
  echo "Expected PR merge commit with exactly two parents" >&2
  exit 1
fi
# Both parents must be available; missing objects must never mean 'skip'.
for parent_sha in "$first_parent" "$second_parent"; do
  git rev-parse --verify --end-of-options "${parent_sha}^{commit}" > /dev/null
done

changed_files="$(mktemp)"
trap 'rm -f "$changed_files"' EXIT
# Pair the tested merge with its own base, not a potentially stale event base
# SHA. Keep deletions and both sides of a rename visible to path selection.
git diff --name-only --no-renames -z "$first_parent" "$merge_sha" -- \
  apps/evolverun/clawweb .github/workflows/clawweb-ci.yml > "$changed_files"
if [ -s "$changed_files" ]; then
  echo "skip=false" >> "$GITHUB_OUTPUT"
else
  echo "skip=true" >> "$GITHUB_OUTPUT"
  echo "No ClawWeb changes; skipping build and tests."
fi
