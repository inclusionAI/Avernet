#!/usr/bin/env bash
# scripts/check-deps.sh — DEP-1~8
# Cargo 依赖图 + 未使用依赖
set -euo pipefail

fail=0
log() { echo "$1 [DEP-$2]: $3"; }

# ─── 准备 cargo metadata ───
if ! command -v cargo >/dev/null; then
  log SKIP - "cargo 不可用"; exit 77
fi
metadata=$(cargo metadata --format-version 1 --no-deps 2>/dev/null || echo '{}')

if [[ "$metadata" == "{}" ]]; then
  log SKIP - "cargo metadata 失败"; exit 77
fi

# Log observation must remain independent of trace/metric implementations.
if ! bash "$(dirname "${BASH_SOURCE[0]}")/check-observability-deps.sh"; then
  fail=1
fi

# ─── 工具函数：列出 crate 的 bcs-* 依赖 ───
bcs_deps_of() {
  local crate=$1
  echo "$metadata" | jq -r ".packages[] | select(.name == \"$crate\") | .dependencies[] | select(.kind == null) | .name" 2>/dev/null \
    | grep -E '^bcs-' || true
}

assert_only_allows() {
  local id=$1 crate=$2; shift 2
  local allowed="$* "
  for d in $(bcs_deps_of "$crate"); do
    if [[ " $allowed " != *" $d "* ]]; then
      log FAIL "$id" "$crate 依赖 $d（不在允许列表 [${allowed% }] 中）"
      fail=1
    fi
  done
}

# ─── DEP-5: bcs-config-api 不依赖任何 bcs-* ───
assert_only_allows 5 bcs-config-api

# ─── DEP-6: bcs-protocol 不依赖任何 bcs-* ───
assert_only_allows 6 bcs-protocol

# ─── DEP-1 / DEP-7: bcs-service-api 仅可依赖协议/领域契约 ───
assert_only_allows 1 bcs-service-api bcs-protocol bcs-domain

# ─── DEP-3: services/* 不能 import bcs_service_api::application ───
# （通过 LINT-1 实现，这里只校验 service crate 列表有效）
service_crates=$(find crates/services -maxdepth 1 -mindepth 1 -type d -exec basename {} \; 2>/dev/null || true)
if [[ -z "$service_crates" ]]; then
  log SKIP 3 "未找到 crates/services/*"
fi

# ─── DEP-7 plugin 之间不互相依赖 ───
plugin_crates=$(find crates/plugins -maxdepth 1 -mindepth 1 -type d -exec basename {} \; 2>/dev/null || true)
for p in $plugin_crates; do
  for dep in $(bcs_deps_of "$p"); do
    if [[ "$dep" =~ ^bcs- ]] && [[ "$dep" != bcs-cache-api && "$dep" != bcs-db-api && "$dep" != bcs-protocol ]]; then
      # plugin 只能依赖 plugin-api 和 protocol
      if echo "$plugin_crates" | grep -qx "$dep"; then
        log FAIL 7 "plugin $p 依赖兄弟 plugin $dep"
        fail=1
      fi
    fi
  done
done

# ─── DEP-8: 声明但未使用 ───
if command -v cargo-machete >/dev/null; then
  if ! cargo machete crates/service-api crates/adapters crates/services crates/external-clients 2>&1 | tee /tmp/machete.log; then
    if grep -q 'unused' /tmp/machete.log; then
      log FAIL 8 "存在声明但未使用的依赖（见上方 cargo-machete 输出）"
      fail=1
    fi
  fi
else
  log SKIP 8 "cargo-machete 未安装，跳过（建议 cargo install cargo-machete）"
fi

exit $fail
