#!/usr/bin/env bash
# 启动纯 Python 谁是卧底玩家；真实对局需要 BCS 已启动且本机有模型配置。
set -euo pipefail
cd "$(dirname "$0")"
exec uv run python -m undercover_player "$@"
