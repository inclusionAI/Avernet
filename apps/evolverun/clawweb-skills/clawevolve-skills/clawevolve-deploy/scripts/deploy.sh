#!/usr/bin/env bash
# deploy.sh — clawevolve-deploy 入口。把 clawevolve-pack 产出的镜像铺到目标 bot workspace，
# 复现原 bot 的 md/mcp/skill 配置层。详见 ../SKILL.md。
#
# 真正的逻辑在 deploy.py（三段式：校验镜像 → 备份+铺入 → 校验铺出，失败回滚）。
# 本脚本只做：轻量环境探测 + 转发参数 + 执行 python。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$HERE/deploy.py"

if [ ! -f "$PY" ]; then
  echo "clawevolve-deploy: 缺少 $PY" >&2
  exit 2
fi

if command -v python3 >/dev/null 2>&1; then
  PYBIN=python3
elif command -v python >/dev/null 2>&1; then
  PYBIN=python
else
  echo "clawevolve-deploy: 需要 python3（未找到）" >&2
  exit 2
fi

# 默认目标 workspace 探测（可被 --workspace 覆盖）。探测顺序与 deploy.py 一致。
if [ -z "${WORKSPACE:-}" ]; then
  if [ -n "${OPENCLAW_HOME:-}" ] && [ -d "${OPENCLAW_HOME:-}/workspace" ]; then
    WORKSPACE="${OPENCLAW_HOME}/workspace"
  elif [ -d "$HOME/.openclaw/workspace" ]; then
    WORKSPACE="$HOME/.openclaw/workspace"
  fi
fi

"$PYBIN" "$PY" "$@"
exit $?