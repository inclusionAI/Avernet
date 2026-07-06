#!/usr/bin/env bash
# scripts/check-import-rules.sh — LINT-1
# 强制 use 路径白名单（CONTEXT.md §2 调用方向）
set -euo pipefail

if ! command -v rg >/dev/null; then
  echo "SKIP [LINT-1]: ripgrep 未安装"; exit 77
fi

fail=0

# 规则格式：<路径 glob><TAB><禁用 use 子串><TAB><doc 引用>
# 注释行（#）跳过
RULES=$(cat <<'EOF'
crates/adapters/**/*.rs	use bcs_service_api::core::	CONTEXT.md §2 / DEP-2
crates/adapters/**/*.rs	use bcs_service_api::port::	CONTEXT.md §2 / DEP-2
crates/services/**/*.rs	use bcs_service_api::application::	CONTEXT.md §2 / DEP-3
crates/external-clients/**/*.rs	use bcs_service_api::core::	CONTEXT.md §2 / DEP-4
crates/external-clients/**/*.rs	use bcs_service_api::application::	CONTEXT.md §2 / DEP-4
EOF
)

# P2 启用（bcs-service-api/src 重组后）
# 检测目录是否存在再启用
SAPI=crates/service-api/bcs-service-api/src
if [[ -d "$SAPI/application" && -d "$SAPI/core" && -d "$SAPI/port" ]]; then
  echo "[LINT-1] 检测到 application/core/port 子目录，启用 P2 规则"
  RULES+=$'\n'"$SAPI/core/**/*.rs	use crate::application::	CONTEXT.md §6.1"
  RULES+=$'\n'"$SAPI/port/**/*.rs	use crate::application::	CONTEXT.md §6.1"
  RULES+=$'\n'"$SAPI/port/**/*.rs	use crate::core::	CONTEXT.md §6.1"
  RULES+=$'\n'"$SAPI/types/**/*.rs	use crate::application::	CONTEXT.md §6.1"
  RULES+=$'\n'"$SAPI/types/**/*.rs	use crate::core::	CONTEXT.md §6.1"
  RULES+=$'\n'"$SAPI/types/**/*.rs	use crate::port::	CONTEXT.md §6.1"
else
  echo "[LINT-1] 未检测到 application/core/port，跳过 P2 内部模块规则"
fi

while IFS=$'\t' read -r path_glob pattern doc; do
  [[ -z "$path_glob" || "$path_glob" =~ ^# ]] && continue
  # 用 rg 直接支持 glob
  if rg --no-heading -n -F "$pattern" -g "$path_glob" . 2>/dev/null; then
    echo "FAIL [LINT-1]: matched \"${pattern}\" (${doc})"
    fail=1
  fi
done <<< "$RULES"

exit $fail
