#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

base=""
head="HEAD"
dry_run="${OCB_PRE_PUSH_DRY_RUN:-0}"
skip_singlebox_coverage="${OCB_PRE_PUSH_SKIP_SINGLEBOX_COVERAGE:-0}"

# 这个脚本既可以被 .githooks/pre-push 调用,也可以手动运行:
#   scripts/ci/pre_push.sh --base origin/dev --head HEAD
# base/head 用来计算“本次变更文件”,从而只跑受影响模块的 CI gate。
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --base requires an argument" >&2
        exit 2
      fi
      base="$2"
      shift 2
      ;;
    --head)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --head requires an argument" >&2
        exit 2
      fi
      head="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --skip-singlebox-coverage)
      skip_singlebox_coverage=1
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$base" ]]; then
  # 手动运行时如果没传 base,默认和 origin/dev 比较。
  # 这和 PR 场景的目标分支思路一致,避免把整个历史都算作本次变更。
  if git rev-parse --verify origin/dev >/dev/null 2>&1; then
    base="$(git merge-base "$head" origin/dev)"
  else
    base="$(git rev-list --max-parents=0 "$head" | tail -1)"
  fi
fi

changed_files="$(git diff --name-only "$base" "$head")"
singlebox_coverage_ran=0

matches_any() {
  # 判断本次变更文件里是否命中某个路径正则。
  # 后面的模块分发都基于这个函数,例如 '^src/backend/'。
  local pattern="$1"
  printf '%s\n' "$changed_files" | grep -Eq "$pattern"
}

run_required() {
  # required gate: 命令失败就让 pre-push 失败,阻止 push。
  echo ""
  echo "== required: $* =="
  if [[ "$dry_run" == "1" ]]; then
    echo "dry-run: command skipped"
    return 0
  fi
  "$@"
}

run_singlebox_coverage_once() {
  if [[ "$skip_singlebox_coverage" == "1" ]]; then
    echo "singlebox coverage skipped (OCB_PRE_PUSH_SKIP_SINGLEBOX_COVERAGE=1)"
    return 0
  fi
  if [[ "$singlebox_coverage_ran" == "1" ]]; then
    echo "singlebox coverage already ran; skipping duplicate"
    return 0
  fi
  run_required "$repo_root/scripts/ci/singlebox_coverage.sh"
  singlebox_coverage_ran=1
}

echo "base: $base"
echo "head: $head"

if [[ -z "$changed_files" ]]; then
  # pre-push 只检查已提交的 push range。
  # 工作区未提交改动不在这里检查,这是 git hook 的自然边界。
  echo "no committed changes in push range; CI gates skipped"
  exit 0
fi

if matches_any '^src/backend/'; then
  # Backend 默认强卡点:
  # 1) python_sast_local.sh 近似线上 python-sast block 规则,且只扫本次变更 Python 文件
  # 2) backend ci_test.sh 跑 pytest + coverage + report_check
  # 3) singlebox coverage 默认并入 pre-push,保证 Backend 变更会跑 singlebox E2E 覆盖率入口。
  run_required "$repo_root/scripts/ci/python_sast_local.sh" src/backend 1 --base "$base" --head "$head"
  run_required "$repo_root/src/backend/scripts/ci_test.sh" --base "$base" --head "$head"
  run_singlebox_coverage_once
fi

if matches_any '^src/baas/'; then
  # BaaS 默认强卡点:
  # 1) 跑 BaaS 自己的 ci_test.sh
  # 2) 默认同样跑 singlebox coverage,用于保证 Backend + BaaS live E2E 入口被触发。
  # 如需临时跳过 BaaS CI,显式设置 OCB_PRE_PUSH_ENABLE_BAAS=0。
  if [[ "${OCB_PRE_PUSH_ENABLE_BAAS:-1}" == "1" ]]; then
    run_required "$repo_root/scripts/ci/python_sast_local.sh" src/baas 1 --base "$base" --head "$head"
    run_required "$repo_root/src/baas/scripts/ci_test.sh" --base "$base" --head "$head"
    run_singlebox_coverage_once
  else
    echo "BaaS changes detected; BaaS CI gate skipped (OCB_PRE_PUSH_ENABLE_BAAS=0)"
  fi
fi

if matches_any '^src/engine/'; then
  # Engine 默认强卡点:
  # SAST 先兜底语法/高危 lint,再跑 engine 自己的 pytest/coverage gate。
  run_required "$repo_root/scripts/ci/python_sast_local.sh" src/engine 1 --base "$base" --head "$head"
  run_required "$repo_root/src/engine/scripts/ci_test.sh" --base "$base" --head "$head"
fi

if matches_any '^src/bcs/'; then
  # BCS/BCN 默认强卡点。
  # pre-push 走 --fast-fail: 第一个失败就退出,节省本地等待时间。
  # 如需临时跳过,显式设置 OCB_PRE_PUSH_ENABLE_BCS=0。
  if [[ "${OCB_PRE_PUSH_ENABLE_BCS:-1}" == "1" ]]; then
    run_required "$repo_root/src/bcs/scripts/ci_test.sh" --base "$base" --head "$head" --fast-fail
  else
    echo "BCS/BCN changes detected; BCS/BCN CI gate skipped (OCB_PRE_PUSH_ENABLE_BCS=0)"
  fi
fi

if matches_any '^src/frontend/'; then
  # Frontend 也通过模块自己的 ci_test.sh 作为统一入口。
  run_required "$repo_root/src/frontend/scripts/ci_test.sh" --base "$base" --head "$head"
fi

if matches_any '^(scripts/singlebox\.sh|scripts/modules/|scripts/ci/singlebox_coverage\.sh|src/backend/tests/community/acceptance/|src/baas/tests/e2e/)'; then
  # singlebox 自身脚本或 live E2E 用例变更时,即便没有 Backend/BaaS 源码变更,
  # 也要触发 singlebox coverage gate。
  run_singlebox_coverage_once
fi

echo ""
echo "OCB pre-push gate passed"
