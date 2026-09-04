#!/usr/bin/env bash
# Start the agentcompute HTTP API (FastAPI/uvicorn).
#
# Usage:
#   ./scripts/serve.sh                 # bind 0.0.0.0:8000 from config/application.yaml
#   ./scripts/serve.sh dev             # merge config/application-dev.yaml (DEPLOY_ENV=dev)
#   PORT=9000 ./scripts/serve.sh       # override the bind port
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV="${1:-}"
if [[ -n "$ENV" ]]; then
  export DEPLOY_ENV="$ENV"
fi

PORT="${PORT:-}"
HOST="${HOST:-0.0.0.0}"

ARGS=(serve --config config/application.yaml)
[[ -n "$HOST" ]] && ARGS+=(--host "$HOST")
[[ -n "$PORT" ]] && ARGS+=(--port "$PORT")

if command -v uv >/dev/null 2>&1; then
  exec uv run acd "${ARGS[@]}"
fi

PYTHONPATH=src python3 -m agentcompute.cli "${ARGS[@]}"