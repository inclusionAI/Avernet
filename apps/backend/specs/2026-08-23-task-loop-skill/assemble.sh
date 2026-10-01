#!/usr/bin/env bash
# 入口:重新生成 task-loop skill 包(调用同目录 assemble.py)。
set -euo pipefail
d="$(cd "$(dirname "$0")" && pwd)"
python3 "$d/assemble.py"
