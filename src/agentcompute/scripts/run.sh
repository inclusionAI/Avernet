#!/usr/bin/env bash
# Start the agent compute demo: plan a goal into a DAG, execute it, and
# write the plan JSON + DAG visualization.
#
# Usage:
#   ./scripts/run.sh "Your goal here"
#   ./scripts/run.sh "Your goal" "searcher:finds,summarizer:summarizes"
#
# Environment (also read from .env):
#   PROVIDER      LLM provider: stub | openai   (default: stub)
#   MAX_WORKERS   concurrent executors           (default: 4)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GOAL="${1:-Summarize the top AI trends for 2026}"
AGENTS="${2:-searcher:Searches sources,summarizer:Summarizes findings}"
PROVIDER="${PROVIDER:-stub}"
MAX_WORKERS="${MAX_WORKERS:-4}"
OUTPUT_DIR="${OUTPUT_DIR:-output}"

mkdir -p "$OUTPUT_DIR"

if command -v uv >/dev/null 2>&1; then
  uv run acd "$GOAL" \
    -a "$AGENTS" \
    --provider "$PROVIDER" \
    --max-workers "$MAX_WORKERS" \
    --plan "$OUTPUT_DIR/plan.json" \
    --viz "$OUTPUT_DIR/dag.html"
else
  PYTHONPATH=src python3 -m agentcompute.cli "$GOAL" \
    -a "$AGENTS" \
    --provider "$PROVIDER" \
    --max-workers "$MAX_WORKERS" \
    --plan "$OUTPUT_DIR/plan.json" \
    --viz "$OUTPUT_DIR/dag.html"
fi

echo ""
echo "DAG plan:    $OUTPUT_DIR/plan.json"
echo "DAG viz:     $OUTPUT_DIR/dag.html"