#!/usr/bin/env bash
set -euo pipefail

target_path="${1:?usage: python_sast_local.sh <path> [major_limit]}"
major_limit="${2:-1}"
shift || true
shift || true
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

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

block_rules=(
  E999 E902 E117 F405 E712 E701 E702 F821 F822 F823 F831
  FLA001 FLA002 FLA003 FLA004 FLA005 FLA006 FLA007 FLA008 FLA009 FLA010
)

select_csv="$(IFS=,; echo "${block_rules[*]}")"

echo "python-sast local block scan"
echo "target: $target_path"
echo "block rules: $select_csv"
echo "major limit from ACI template: $major_limit"

scan_targets=("$target_path")
if [[ -n "$base" ]]; then
  changed_py=()
  while IFS= read -r file; do
    [[ -n "$file" ]] && changed_py+=("$file")
  done < <(git diff --name-only --diff-filter=AM "$base" "$head" -- "$target_path" | grep -E '\.py$' || true)
  if [[ "${#changed_py[@]}" -eq 0 ]]; then
    echo "no changed Python files under $target_path; python-sast local scan skipped"
    exit 0
  fi
  scan_targets=("${changed_py[@]}")
fi

if [[ -n "${PYTHON_SAST_CMD:-}" ]]; then
  # Shell words are intentional here so users can point to the exact company CLI.
  # Example: PYTHON_SAST_CMD='python-sast scan --format text'
  # shellcheck disable=SC2086
  exec $PYTHON_SAST_CMD "${scan_targets[@]}"
fi

if command -v antflake >/dev/null 2>&1; then
  exec antflake "${scan_targets[@]}" --select "$select_csv"
fi

if command -v flake8 >/dev/null 2>&1; then
  exec flake8 "${scan_targets[@]}" --select "$select_csv"
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run --with flake8 flake8 "${scan_targets[@]}" --select "$select_csv"
fi

echo "error: uv/flake8 not found; cannot run local python-sast block scan" >&2
exit 127
