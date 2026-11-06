#!/usr/bin/env bash
# Verify that Claude profiles are not restricted to the historical platform-data role.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PROFILE="$TMP/claude-profile"
cp -R "$ROOT/singlebox/agents/4bots_merchant_operations_profile_for_claude" "$PROFILE"
mkdir -p "$PROFILE/player-luoji" "$TMP/claude-config" "$TMP/claude-workspace"
cp "$PROFILE/platform-data/"*.md "$PROFILE/player-luoji/"

python3 - "$PROFILE/bots.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
data = json.loads(manifest.read_text(encoding="utf-8"))
bot = data["bots"][0]
bot.update(
    source="player-luoji",
    profile="undercover-player-luoji",
    name="玩家逻辑控林工",
    summary="Claude Code 驱动的谁是卧底玩家。",
    domains="undercover-game,social-deduction",
    skills="word-description,deductive-voting",
)
bot["runtime"].update(
    claude_config_dir=str(manifest.parent.parent / "claude-config"),
    workspace=str(manifest.parent.parent / "claude-workspace"),
    system_prompt_md="player-luoji/CLAUDE.md",
)
manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
PY

export CLAUDE_PROFILE_DIR="$PROFILE"
export BOTS_PROFILE_DIR="$ROOT/singlebox/agents/6bots_undercover_game_profile"
# shellcheck source=/dev/null
source "$ROOT/singlebox/singlebox.sh"

claude_profile_validate_config

IFS=$'\x1f' read -r source name _ _ _ _ _ prompt _ < <(claude_profile_entries)
[[ "$source" == "player-luoji" ]]
[[ "$name" == "玩家逻辑控林工" ]]
[[ "$prompt" == "$PROFILE/player-luoji/CLAUDE.md" ]]

python3 - "$PROFILE/bots.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
data["bots"][0]["runtime"]["system_prompt_md"] = "../CLAUDE.md"
json.dump(data, open(sys.argv[1], "w", encoding="utf-8"))
PY

if claude_profile_validate_config; then
    printf '%s\n' "profile traversal was accepted" >&2
    exit 1
fi

test_invalid_profile_manifest_is_rejected() {
    local case_name="$1"
    local mutation="$2"
    local case_dir="$TMP/profile-${case_name}"
    local manifest="$case_dir/bots.json"
    rm -rf "$case_dir"
    cp -R "$ROOT/singlebox/agents/4bots_merchant_operations_profile_for_claude" "$case_dir"
    mkdir -p "$case_dir/player-luoji"
    cp "$case_dir/platform-data/"*.md "$case_dir/player-luoji/"
    python3 - "$manifest" "$mutation" <<'PY'
import json
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
mutation = sys.argv[2]
if mutation == "malformed":
    manifest.write_text("{not-json", encoding="utf-8")
    raise SystemExit(0)

data = json.loads(manifest.read_text(encoding="utf-8"))
bot = data["bots"][0]
if mutation == "missing-name":
    del data["name"]
elif mutation == "wrong-runtime-type":
    bot["runtime"]["type"] = "openclaw"
elif mutation == "wrong-port-type":
    bot["runtime"]["relay_port"] = "18900"
elif mutation == "missing-prompt":
    bot["runtime"]["system_prompt_md"] = "player-luoji/MISSING.md"
elif mutation == "multiple-bots":
    data["bots"].append(dict(bot))
else:
    raise SystemExit(f"unknown mutation: {mutation}")
manifest.write_text(json.dumps(data), encoding="utf-8")
PY
    CLAUDE_PROFILE_DIR="$case_dir"
    if claude_profile_validate_config; then
        printf 'invalid profile accepted: %s\n' "$case_name" >&2
        return 1
    fi
}

test_invalid_profile_manifest_is_rejected malformed malformed
test_invalid_profile_manifest_is_rejected missing-name missing-name
test_invalid_profile_manifest_is_rejected wrong-runtime-type wrong-runtime-type
test_invalid_profile_manifest_is_rejected wrong-port-type wrong-port-type
test_invalid_profile_manifest_is_rejected missing-prompt missing-prompt
test_invalid_profile_manifest_is_rejected multiple-bots multiple-bots

printf '%s\n' "generic Claude profile validation passed"
