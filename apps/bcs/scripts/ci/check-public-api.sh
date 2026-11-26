#!/usr/bin/env bash
# scripts/ci/check-public-api.sh — API-1 / D0 public-API diff gate
#
# crates/service-api/* 的 pub 项变更必须升版本；breaking 变更必须跨过兼容
# 边界（1.x → major，0.x → minor）；bcs-dist-model 单独做版本 + 序列化 wire
# 兼容检查（旧格式 persistence_shape 与新格式 group_round_wire 均须保持绿）。
#
# 差异模式（不强制 checkout，允许脏工作区）：
#   baseline = 临时 `git worktree add --detach <dir> <base-commit>`（独立
#              工作树，绝不触碰当前树；cargo-public-api 0.52.0 的
#              `diff <refA>..<refB>` 会对当前树做 in-place git checkout，
#              脏工作区下必须避开）
#   worktree = 当前工作树（含未提交修改）
#   两侧各取 `cargo public-api -p <crate>` 清单，再对清单做统一 diff。
#   工具任一侧失败都会 FAIL 并展示 stderr，不吞错误。
#
# 工具安装与固定（CI 同步安装）：
#   rustup toolchain install nightly-2025-11-30 --profile minimal
#   CARGO_INSTALL_ROOT=<repo>/.superpowers/tools \
#     cargo install cargo-public-api --version 0.52.0 --locked
# pin：nightly-2025-11-30（rustdoc JSON 版本配套）+ cargo-public-api 0.52.0。
# 可用 CARGO_PUBLIC_API_BIN 覆盖工具路径、CARGO_PUBLIC_API_NIGHTLY 覆盖
# nightly pin。工具或 nightly 缺失时 SKIP(77)；真实比较必须真 PASS/FAIL。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/ci-env.sh"
BCS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPO_TOP="$(git -C "$BCS_ROOT" rev-parse --show-toplevel)"
BCS_PREFIX="$(git -C "$BCS_ROOT" rev-parse --show-prefix)"
# 共享本 workspace 既有 target（复用依赖产物；禁止另开/清理缓存）。
CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$BCS_ROOT/target}"
export CARGO_TARGET_DIR

PR_BODY_FILE=${PR_BODY_FILE:-/tmp/pr_body.txt}
NIGHTLY_TOOLCHAIN=${CARGO_PUBLIC_API_NIGHTLY:-nightly-2025-11-30}
ARTIFACT_DIR=${PUBLIC_API_ARTIFACT_DIR:-}

PREREQUISITE_EXIT=77
if [[ "${CI:-false}" == "true" ]]; then PREREQUISITE_EXIT=1; fi

if ! command -v cargo >/dev/null; then
  echo "API-1 unavailable: cargo 不可用"; exit "$PREREQUISITE_EXIT"
fi

pubapi_bin=""
for cand in "${CARGO_PUBLIC_API_BIN:-}" \
            "$REPO_TOP/.superpowers/tools/bin/cargo-public-api" \
            "$(command -v cargo-public-api 2>/dev/null || true)"; do
  if [[ -n "$cand" && -x "$cand" ]]; then pubapi_bin="$cand"; break; fi
done

if [[ -z "$pubapi_bin" ]]; then
  echo "SKIP [API-1]: cargo-public-api 未安装"
  echo "  （建议：CARGO_INSTALL_ROOT=<repo>/.superpowers/tools cargo install cargo-public-api --version 0.52.0 --locked）"
  exit "$PREREQUISITE_EXIT"
fi

if ! rustup toolchain list 2>/dev/null | grep -q "$NIGHTLY_TOOLCHAIN"; then
  echo "SKIP [API-1]: 未安装 pinned nightly toolchain $NIGHTLY_TOOLCHAIN（rustdoc JSON 需要）"
  exit "$PREREQUISITE_EXIT"
fi

fail=0

# baseline commit：取 base ref 与 HEAD 的 merge-base，rebase 分支/旧 base ref
# 都能收敛到正确的基线，不做 in-place checkout。
BASE_REF=$(resolve_bcs_base_ref "$BCS_ROOT")
BASE_COMMIT=$(git -C "$BCS_ROOT" merge-base "$BASE_REF" HEAD 2>/dev/null) || {
  echo "FAIL [API-1]: 无法解析 baseline merge-base（ref=$BASE_REF）"
  exit 1
}

BASE_WORKTREE=""
cleanup() {
  if [[ -n "$BASE_WORKTREE" && -e "$BASE_WORKTREE/.git" ]]; then
    git -C "$BCS_ROOT" worktree remove --force "$BASE_WORKTREE" >/dev/null 2>&1 || true
    rm -rf "$BASE_WORKTREE"
  fi
}
trap cleanup EXIT

if [[ -z "$ARTIFACT_DIR" ]]; then
  ARTIFACT_DIR=$(mktemp -d /tmp/pubapi-gate.XXXXXX)
else
  mkdir -p "$ARTIFACT_DIR"
fi

# baseline worktree 固定放系统 temp（不放进主仓库树内，避免嵌套工作树）。
BASE_WORKTREE=$(mktemp -d /tmp/pubapi-baseline-worktree.XXXXXX)
rmdir "$BASE_WORKTREE" # worktree add 需要不存在的路径
git -C "$BCS_ROOT" worktree add --detach --quiet "$BASE_WORKTREE" "$BASE_COMMIT"

# capture_api <dir> <crate> <out-prefix>: 输出清单到 <prefix>.txt，
# stderr 到 <prefix>.err；工具失败返回非零（不吞错误）。
capture_api() {
  local dir=$1 crate=$2 prefix=$3 rc=0
  (cd "$dir" && \
   CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-12}" \
   CARGO_PROFILE_DEV_DEBUG=line-tables-only \
   RUSTUP_TOOLCHAIN="$NIGHTLY_TOOLCHAIN" \
   "$pubapi_bin" -p "$crate" --color never \
   >"$prefix.txt" 2>"$prefix.err") || rc=$?
  return $rc
}

report_tool_error() { # <crate> <side> <errfile>
  echo "FAIL [API-1]: $1 的 $2 侧清单提取失败（stderr 尾部，全文见 $3）："
  tail -n 15 "$3" | sed 's/^/  /'
}

crate_toml_version() { # <toml-text-file> → version 字段
  grep -E '^version[[:space:]]*=' "$1" | head -1 | sed -E 's/.*"(.+)".*/\1/'
}

# check_crate <crate> <crate_path>: 对比两侧清单并执行版本规则。
# 仅打勾“与 baseline 不同”时才检查版本；breaking = 任一 `-` 行。
check_crate() {
  local crate=$1 crate_path=$2 base_out head_out base_ver head_ver
  base_out="$ARTIFACT_DIR/${crate}.base.txt"
  head_out="$ARTIFACT_DIR/${crate}.head.txt"

  if ! capture_api "$BASE_WORKTREE/$BCS_PREFIX" "$crate" "${ARTIFACT_DIR}/${crate}.base"; then
    report_tool_error "$crate" baseline "${ARTIFACT_DIR}/${crate}.base.err"
    fail=1
    return 1
  fi
  if ! capture_api "$BCS_ROOT" "$crate" "${ARTIFACT_DIR}/${crate}.head"; then
    report_tool_error "$crate" worktree "${ARTIFACT_DIR}/${crate}.head.err"
    fail=1
    return 1
  fi

  if cmp -s "$base_out" "$head_out"; then
    echo "PASS [API-1]: $crate 公开 API 无变化"
    return 0
  fi

  echo "[API-1] $crate 的公开 API 发生变化："
  diff -u "$base_out" "$head_out" \
    | sed -E 's/^(---|\+\+\+|@@)/  \1/' | sed 's/^-/  -/; s/^+/  +/' || true
  echo "  （完整清单：$base_out vs $head_out）"

  # 版本规则
  crate_bcs_path="crates/$crate_path"
  base_ver=$(git -C "$BCS_ROOT" show "$BASE_COMMIT:$BCS_PREFIX$crate_bcs_path/Cargo.toml" \
    | grep -E '^version[[:space:]]*=' | head -1 | sed -E 's/.*"(.+)".*/\1/') || base_ver=""
  head_ver=$(crate_toml_version "$BCS_ROOT/$crate_bcs_path/Cargo.toml")

  if [[ -z "$base_ver" ]]; then
    echo "FAIL [API-1]: $crate 无法读取 base 版本，版本规则无法执行"
    fail=1
    return 1
  fi
  if [[ "$head_ver" == "$base_ver" ]]; then
    echo "FAIL [API-1]: ${crate} 公开 API 变化但版本未变（${base_ver} -> ${head_ver}）"
    fail=1
  fi

  # breaking 判定基于清单行：baseline 独有项被删除/签名改变即 breaking。
  local has_breaking=false
  if [[ -n "$(comm -23 <(sort "$base_out") <(sort "$head_out"))" ]]; then
    has_breaking=true
  fi
  local break_threshold base_boundary head_boundary
  if [[ "$base_ver" == 0.* ]]; then
    # 0.x：minor 是兼容边界（0.1.0 → 0.2.0）
    break_threshold=minor
    base_boundary=$(echo "$base_ver" | cut -d. -f1-2)
    head_boundary=$(echo "$head_ver" | cut -d. -f1-2)
  else
    break_threshold=major
    base_boundary=$(echo "$base_ver" | cut -d. -f1)
    head_boundary=$(echo "$head_ver" | cut -d. -f1)
  fi

  if $has_breaking && [[ "$base_boundary" == "$head_boundary" ]]; then
    echo "FAIL [API-1]: ${crate} 有 breaking 公开 API 变化但未跨过兼容边界（${base_ver} -> ${head_ver}，需要 ${break_threshold} bump）"
    fail=1
  fi

  # PR 必须含 propagation analysis（本地无 PR 正文文件时不检查）
  if [[ -f "$PR_BODY_FILE" ]]; then
    if ! grep -qiE '影响的[[:space:]]*consumer' "$PR_BODY_FILE"; then
      echo "FAIL [API-1]: $crate 修改了 pub API，PR 描述缺 Propagation analysis"
      fail=1
    fi
  fi
  return 0
}

for crate_path in \
    "service-api/bcs-config-api" \
    "contracts/bcs-protocol" \
    "service-api/bcs-service-api"; do
  crate=$(basename "$crate_path")
  if [[ ! -d "$BCS_ROOT/crates/$crate_path" ]]; then
    echo "FAIL [API-1]: 门禁覆盖的 crate 目录缺失：crates/$crate_path"
    fail=1
    continue
  fi
  check_crate "$crate" "$crate_path"
done

# ---- bcs-dist-model：单独的版本 + 序列化兼容检查 -------------------------
# model crate 是合同数据面：除版本规则外，公开 API 变化时还要求旧格式
# wire 回归测试保持绿（序列化兼容以真实 serde 往返为准，不靠清单本身）。
model_path="contracts/bcs-dist-model"
if [[ -d "$BCS_ROOT/crates/$model_path" ]]; then
  if check_crate "bcs-dist-model" "$model_path"; then
    if ! cmp -s "$ARTIFACT_DIR/bcs-dist-model.base.txt" "$ARTIFACT_DIR/bcs-dist-model.head.txt"; then
      echo "[API-1] bcs-dist-model 序列化兼容检查：cargo test -p bcs-dist-model --test group_round_wire --test persistence_shape"
      model_wise_log="$ARTIFACT_DIR/bcs-dist-model.wire.log"
      model_rc=0
      (cd "$BCS_ROOT" && unset RUSTUP_TOOLCHAIN && \
       CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-12}" \
       CARGO_PROFILE_DEV_DEBUG=line-tables-only \
       cargo test -p bcs-dist-model --test group_round_wire --test persistence_shape --color never \
       >"$model_wise_log" 2>&1) || model_rc=$?
      if [[ "$model_rc" -ne 0 ]]; then
        echo "FAIL [API-1]: bcs-dist-model 序列化 wire 兼容测试失败（尾部输出，全文 $model_wise_log）："
        tail -n 15 "$model_wise_log" | sed 's/^/  /'
        fail=1
      else
        echo "PASS [API-1]: bcs-dist-model 旧格式 wire 兼容测试通过（$(grep -c 'test result:' "$model_wise_log") 个 test target 绿）"
      fi
    fi
  fi
else
  echo "FAIL [API-1]: 缺少 $model_path，model 兼容检查未执行"
  fail=1
fi

if [[ $fail -eq 0 ]]; then
  echo "PASS [API-1]: 公开 API 门禁通过（baseline=$BASE_COMMIT，worktree 含未提交修改）"
fi
exit $fail
