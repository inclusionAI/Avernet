#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
CONFIG="$TMP/claude-bots.json"

cleanup() {
  if declare -F claude_relays_stop >/dev/null 2>&1; then
    claude_relays_stop || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

python3 - "$CONFIG" "$TMP" <<'PY'
import json
import sys

config_path, root = sys.argv[1:]
roles = (("planner", 18910), ("developer", 18911), ("reviewer", 18912))
bots = []
for role, port in roles:
    bots.append({
        "role": role,
        "relay_port": port,
        "claude_config_dir": f"{root}/{role}/config",
        "workspace": f"{root}/{role}/workspace",
    })
json.dump({"bots": bots}, open(config_path, "w", encoding="utf-8"))
PY

for role in planner developer reviewer; do
  mkdir -p "$TMP/$role/config"
done

CLAUDE_BOTS_CONFIG="$CONFIG"
# shellcheck source=/dev/null
source "$ROOT/scripts/singlebox.sh"

# The relay must not blindly use a broken self-updating native binary when an
# existing npm CLI is healthy.  Stub only the no-side-effect version probe so
# this contract is independent of the host's Claude installation.
claude_relay_cli_usable() {
  [ "$1" = "/opt/homebrew/bin/claude" ]
}
[[ "$(claude_relay_resolve_cli)" = "/opt/homebrew/bin/claude" ]]
if CLAUDE_CODE_PATH="/missing/claude" claude_relay_resolve_cli >/dev/null 2>&1; then
  echo "unusable explicit Claude CLI path unexpectedly accepted" >&2
  exit 1
fi

claude_relays_start

for role in planner developer reviewer; do
  case "$role" in
    planner) port=18910 ;;
    developer) port=18911 ;;
    reviewer) port=18912 ;;
  esac
  curl --noproxy '*' --connect-timeout 1 --max-time 2 -fsS "http://127.0.0.1:${port}/health" | jq -e '.ok == true' >/dev/null
  [ -f "${CLAUDE_RELAY_STATE_DIR}/${role}.pid" ]
  [ -d "${CLAUDE_RELAY_STATE_DIR}/${role}/data" ]
  [ ! -e "$TMP/${role}/workspace/CLAUDE.md" ]
done

claude_relays_stop
echo "three isolated Claude relay health checks passed"
