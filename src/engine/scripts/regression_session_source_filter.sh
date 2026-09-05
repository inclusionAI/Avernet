#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ENGINE_DIR"

PYTHONPATH=src uv run --with pytest --with pytest-asyncio python -m pytest \
  src/engine/community/core/adapters/claude_code/tests \
  src/engine/community/plugins/claude_code/tests \
  src/engine/community/plugin_api/claude_code/tests/test_ports_import.py \
  src/engine/community/api/tests/test_session_router.py \
  src/engine/community/tests/contracts/test_claude_code_local_plugin.py \
  src/engine/community/engines/claude_code/tests \
  -q

printf '%s\n' '[PASS] Claude Code session source filter focused regression'
