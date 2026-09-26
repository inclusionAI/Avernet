#!/usr/bin/env bash
# scripts/check-pr-gate.sh — PR-1
# 修改 crates/service-api/** 或 crates/plugin-api/** 的 PR 必须填架构自检
set -euo pipefail

# 输入：环境变量 PR_BODY_FILE 指向 PR 描述文本文件
# CI 工作流负责把 github.event.pull_request.body dump 到这个文件
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/ci-env.sh"
PR_BODY_FILE=${PR_BODY_FILE:-/tmp/pr_body.txt}
BASE=$(resolve_bcs_base_ref .)
BCS_PREFIX=$(git rev-parse --show-prefix 2>/dev/null || true)

if [[ ! -f "$PR_BODY_FILE" ]]; then
  echo "SKIP [PR-1]: 非 PR 环境，无 PR 描述"; exit 77
fi

# 检查本 PR 是否动了契约文件。git diff 输出始终按仓库根路径，
# 本脚本在 src/bcs 下运行时需要先剥离 BCS 前缀。
changed=$(
  git diff --name-only "$BASE"...HEAD 2>/dev/null \
    | sed -nE "s#^${BCS_PREFIX}(crates/(service-api|plugin-api)/.*)#\\1#p" \
    || true
)
if [[ -z "$changed" ]]; then
  echo "[PR-1] 本 PR 未修改契约 crate，跳过架构自检"
  exit 0
fi

echo "[PR-1] 本 PR 修改了以下契约文件，需要架构自检："
echo "$changed" | sed 's/^/  /'

fail=0
body=$(cat "$PR_BODY_FILE")

# 必需勾选项
if ! grep -qE '\[x\][[:space:]]+契约方向分类' <<<"$body"; then
  echo "FAIL [PR-1]: PR 描述缺 '契约方向分类' 勾选"
  fail=1
fi

# breaking 改动必须有 propagation analysis
if grep -qE '\[x\][[:space:]]+breaking' <<<"$body"; then
  if ! grep -qE '影响的[[:space:]]*consumer' <<<"$body"; then
    echo "FAIL [PR-1]: breaking 改动 PR 必须含 Propagation analysis（含 '影响的 consumer'）"
    fail=1
  fi
  # 检查版本号是否升 major
  echo "[PR-1] 提醒：breaking 改动需在 Cargo.toml 升 major 版本（由 API-1 校验）"
fi

# 新增 Outbound Port 的 PR 必须含 design criteria 论证
new_port=$(echo "$changed" | xargs grep -lE 'pub trait [A-Za-z0-9_]*Port' 2>/dev/null || true)
if [[ -n "$new_port" ]]; then
  for kw in "De-domain" "Infrastructure swap" "Business reuse"; do
    if ! grep -qi "$kw" <<<"$body"; then
      echo "FAIL [PR-1]: PR 引入新 Port，但未提及 Outbound Port Design Criteria 中的 '$kw'"
      fail=1
    fi
  done
fi

# 新增偏离必须有 waiver 编号
if grep -qE '\[x\][[:space:]]+是.*新增偏离' <<<"$body"; then
  if ! grep -qE 'waiver[[:space:]]+编号' <<<"$body"; then
    echo "FAIL [PR-1]: 标记了新增偏离但未提供 waiver 编号"
    fail=1
  fi
fi

exit $fail
