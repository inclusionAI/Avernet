#!/usr/bin/env bash
# Boot the HTTP service, probe /health, then shut down — a startup smoke test.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"

uv run acd serve --config config/application.yaml --host "$HOST" --port "$PORT" &
PID=$!
trap 'kill "$PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  if curl -fsS "http://$HOST:$PORT/health" >/dev/null 2>&1; then
    echo "health ok: http://$HOST:$PORT/health"
    exit 0
  fi
  sleep 0.1
done

echo "server did not become healthy in time" >&2
exit 1