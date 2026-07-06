#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

base=""
head="HEAD"

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base)
      base="$2"
      shift 2
      ;;
    --head)
      head="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$base" ]]; then
  if git rev-parse --verify origin/dev >/dev/null 2>&1; then
    base="$(git merge-base "$head" origin/dev)"
  else
    base="$(git rev-list --max-parents=0 "$head" | tail -1)"
  fi
fi

changed_files="$(git diff --name-only "$base" "$head")"

matches_any() {
  local pattern="$1"
  printf '%s\n' "$changed_files" | grep -Eq "$pattern"
}

run_required() {
  echo ""
  echo "== required: $* =="
  "$@"
}

echo "base: $base"
echo "head: $head"

if [[ -z "$changed_files" ]]; then
  echo "no committed changes in push range; CI gates skipped"
  exit 0
fi

if matches_any '^src/bcs/'; then
  if [[ "${OCB_PRE_PUSH_ENABLE_BCS:-1}" == "1" ]]; then
    run_required "$repo_root/src/bcs/scripts/ci_test.sh" --base "$base" --head "$head" --fast-fail
  else
    echo "bcs changes detected; BCS/BCN CI gate skipped (OCB_PRE_PUSH_ENABLE_BCS=0)"
  fi
fi

echo ""
echo "OCB pre-push gate passed"
