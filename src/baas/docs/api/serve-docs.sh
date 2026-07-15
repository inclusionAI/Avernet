#!/usr/bin/env bash
# Serve the API docs locally so a browser can render the OpenAPI specs.
# A local server is required because browsers block fetching YAML over file://.
#
# Usage:  ./serve-docs.sh [port]      (default 8910)
# Then open the printed URL. Pick a group from the dropdown, or deep-link with
#   http://localhost:<port>/?spec=open-api.yaml
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-8910}"
echo "Serving API docs from: $DIR"
echo "  open:  http://localhost:${PORT}/"
echo "  (Ctrl-C to stop)"
exec python3 -m http.server "$PORT" --directory "$DIR"

