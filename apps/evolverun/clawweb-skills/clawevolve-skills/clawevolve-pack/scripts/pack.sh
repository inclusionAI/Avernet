#!/usr/bin/env bash
# pack.sh — clawevolve-pack 入口。把当前 bot 的 agent 配置物料（md/mcp/skill）
# 打成不可变自包含 .zip 镜像，用于基线回归。详见 ../SKILL.md。
#
# 真正的逻辑在 pack.py（handler 注册表驱动，预留 --with-* 扩展洞）。
# 本脚本只做：轻量环境探测 + 转发参数 + 执行 python。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$HERE/pack.py"

if [ ! -f "$PY" ]; then
  echo "clawevolve-pack: 缺少 $PY" >&2
  exit 2
fi

# python3 探测（沙箱里通常有；没有则报清晰错误）
if command -v python3 >/dev/null 2>&1; then
  PYBIN=python3
elif command -v python >/dev/null 2>&1; then
  PYBIN=python
else
  echo "clawevolve-pack: 需要 python3（未找到）" >&2
  exit 2
fi

# 默认源 workspace 探测（可被 --workspace 覆盖）。候选顺序与 pack.py 一致。
if [ -z "${WORKSPACE:-}" ]; then
  if [ -n "${OPENCLAW_HOME:-}" ] && [ -d "${OPENCLAW_HOME:-}/workspace" ]; then
    WORKSPACE="${OPENCLAW_HOME}/workspace"
  elif [ -d "$HOME/.openclaw/workspace" ]; then
    WORKSPACE="$HOME/.openclaw/workspace"
  fi
fi

# WORKSPACE 留空也没关系——pack.py 会探测并报“找不到 workspace”。
exec "$PYBIN" "$PY" "$@"