#!/usr/bin/env bash
# scripts/check-conformance-entries.sh — TEST-1
# Phase 3: delegate to the centralized R25 conformance gate.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$SCRIPT_DIR/check-r25-conformance.sh"
