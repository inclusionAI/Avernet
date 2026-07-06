#!/usr/bin/env bash
# scripts/check-baseline-not-growing.sh
# baseline 文件只能减不能增——新增违规必须修复，不允许加 baseline
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BCS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/ci-env.sh"
BCS_PREFIX=$(git -C "$BCS_ROOT" rev-parse --show-prefix 2>/dev/null || true)
BASE=$(resolve_bcs_base_ref "$BCS_ROOT")

BASELINE_FILES=(
  "$SCRIPT_DIR/baseline/forbidden-symbols.txt"
  "$SCRIPT_DIR/baseline/trait-naming.txt"
  "$SCRIPT_DIR/baseline/conformance-missing.txt"
)

# 非 PR 环境跳过
if ! git rev-parse "$BASE" >/dev/null 2>&1; then
  echo "SKIP [BASELINE]: 无法解析 ${BASE}，非 PR 环境"
  exit 77
fi

fail=0
for f in "${BASELINE_FILES[@]}"; do
  rel=${f#"$BCS_ROOT/"}
  git_rel="${BCS_PREFIX}${rel}"
  [[ -f "$f" ]] || continue

  if ! git cat-file -e "${BASE}:${git_rel}" 2>/dev/null; then
    head_lines=$(grep -cvE '^\s*(#|$)' "$f" || true)
    head_lines=${head_lines:-0}
    echo "[BASELINE] ${rel}: base=absent, head=${head_lines} (first baseline file; skip growth comparison)"
    continue
  fi

  base_lines=$(git show "${BASE}:${git_rel}" 2>/dev/null | grep -cvE '^\s*(#|$)' || true)
  head_lines=$(grep -cvE '^\s*(#|$)' "$f" || true)
  base_lines=${base_lines:-0}
  head_lines=${head_lines:-0}

  echo "[BASELINE] ${rel}: base=${base_lines}, head=${head_lines}"

  if (( head_lines > base_lines )); then
    echo "FAIL [BASELINE]: ${rel} 增长（${base_lines} → ${head_lines}）。不允许新增偏离——请修复违规而非加 baseline。"
    git diff "$BASE" -- "$git_rel" || true
    fail=1
  fi
done

exit $fail
