#!/usr/bin/env bash
# One invocation, console plus persistent log; pipeline status is never hidden by tee.
set -euo pipefail
umask 077
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
mkdir -p "$HERE/logs"
LOG="$(mktemp "$HERE/logs/console-$(date +%Y%m%dT%H%M%S)-XXXXXX.log")"
printf 'Console log: %s\n' "$LOG"
/opt/conda/bin/python3 -u "$HERE/run.py" "$@" 2>&1 | tee -a "$LOG"
