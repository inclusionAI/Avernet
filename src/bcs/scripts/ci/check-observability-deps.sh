#!/usr/bin/env bash
# Keep log observation independent of trace/metric implementations, including transitive dependencies.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../.."

dependencies=$(cargo tree --all-features -p bcs-observability --edges normal,build --prefix none --format '{p}')
if grep -Eq '^(opentelemetry[^ ]*|tracing-opentelemetry|bcs-telemetry|metrics[^ ]*) ' <<< "$dependencies"; then
  echo 'FAIL: bcs-observability must not depend on OpenTelemetry, bcs-telemetry or metrics implementations'
  exit 1
fi
echo 'PASS: bcs-observability has no trace/metric implementation dependencies'
