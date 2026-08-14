#!/usr/bin/env bash
# scripts/modules/claude_profile.sh — validated one-bot Claude profile input
[[ -n "${_CLAUDE_PROFILE_SH_LOADED:-}" ]] && return 0
_CLAUDE_PROFILE_SH_LOADED=1

claude_profile_enabled() {
    [ -n "${CLAUDE_PROFILE_DIR:-}" ]
}

claude_profile_dir() {
    local configured_dir="${CLAUDE_PROFILE_DIR:-}"
    [ -n "$configured_dir" ] || return 1
    case "$configured_dir" in
        /*) ;;
        *) configured_dir="${PROJECT_ROOT}/${configured_dir}" ;;
    esac
    cd "$configured_dir" 2>/dev/null && pwd -P
}

claude_profile_manifest() {
    printf '%s/bots.json\n' "$(claude_profile_dir)"
}

claude_profile_validate_config() {
    claude_profile_enabled || return 0
    local root manifest
    root="$(claude_profile_dir)" || {
        log_error "Claude profile directory not found: ${CLAUDE_PROFILE_DIR}"
        return 1
    }
    manifest="${root}/bots.json"
    [ -f "$manifest" ] || { log_error "Claude profile manifest not found: ${manifest}"; return 1; }

    if ! python3 - "$root" "$manifest" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
manifest = Path(sys.argv[2])
try:
    data = json.loads(manifest.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid JSON: {exc}")

if not isinstance(data, dict) or set(data) - {"version", "name", "entity_id", "entity_type", "bots"}:
    raise SystemExit("profile contains unsupported top-level fields")
if data.get("version") != 1 or not isinstance(data.get("name"), str) or not data["name"].strip():
    raise SystemExit("profile requires version=1 and a non-empty name")
for field, default in (("entity_id", "mock-user"), ("entity_type", "staff")):
    value = data.get(field, default)
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]+", value):
        raise SystemExit(f"{field} is invalid")

bots = data.get("bots")
if not isinstance(bots, list) or len(bots) != 1 or not isinstance(bots[0], dict):
    raise SystemExit("profile must declare exactly one Claude bot")
bot = bots[0]
required = {"source", "profile", "name", "summary", "domains", "skills", "runtime"}
if set(bot) != required:
    raise SystemExit("Claude bot fields are invalid")
for field in required - {"runtime"}:
    value = bot.get(field)
    if not isinstance(value, str) or not value.strip() or any(ch in value for ch in "\t\r\n"):
        raise SystemExit(f"{field} must be a non-empty single-line string")
if bot["source"] != "platform-data" or bot["profile"] != "platform-data-analyst":
    raise SystemExit("Claude profile must define platform-data analyst")

runtime = bot["runtime"]
required_runtime = {"type", "relay_port", "claude_config_dir", "workspace", "permission_mode", "system_prompt_md"}
if not isinstance(runtime, dict) or set(runtime) != required_runtime:
    raise SystemExit("Claude runtime fields are invalid")
if runtime["type"] != "claude_code" or runtime["relay_port"] != 18900:
    raise SystemExit("Claude runtime must use claude_code relay port 18900")
if runtime["permission_mode"] != "bypassPermissions":
    raise SystemExit("Claude runtime permission_mode must be bypassPermissions")
for field in ("claude_config_dir", "workspace", "system_prompt_md"):
    value = runtime[field]
    if not isinstance(value, str) or not value.strip() or any(ch in value for ch in "\t\r\n"):
        raise SystemExit(f"runtime.{field} must be a non-empty single-line string")
for field in ("claude_config_dir", "workspace"):
    if not os.path.isabs(os.path.expanduser(runtime[field])):
        raise SystemExit(f"runtime.{field} must resolve to an absolute path")

prompt_rel = runtime["system_prompt_md"]
if os.path.isabs(prompt_rel) or ".." in Path(prompt_rel).parts or Path(prompt_rel).suffix.lower() != ".md":
    raise SystemExit("runtime.system_prompt_md must be a relative Markdown path")
prompt_path = (root / prompt_rel).resolve()
try:
    prompt_path.relative_to(root)
except ValueError:
    raise SystemExit("runtime.system_prompt_md escapes the Claude profile")
if prompt_path.name != "CLAUDE.md" or prompt_path.parent.name != "platform-data" or not prompt_path.is_file():
    raise SystemExit("runtime.system_prompt_md must be platform-data/CLAUDE.md")
for filename in ("CLAUDE.md", "WORKFLOW.md", "KNOWLEDGE.md", "RULES.md", "OUTPUT.md", "MEMORY.md"):
    if not (prompt_path.parent / filename).is_file():
        raise SystemExit(f"Claude profile file missing: platform-data/{filename}")
PY
    then
        log_error "Invalid --claude-profile-dir (expected one platform-data Claude Code profile)"
        return 1
    fi
}

# Emits unit-separator-delimited source/name/summary/port/config/workspace/model/prompt/permission.
# The model is resolved from the selected Singlebox runtime configuration, not
# persisted in the reusable Claude role profile.
claude_profile_entries() {
    local manifest runtime_model
    manifest="$(claude_profile_manifest)" || return 1
    runtime_model="${HYBRID_MODEL_ID:-${OPENCLAW_OPENAI_MODEL_ID:-}}"
    python3 - "$manifest" "$runtime_model" <<'PY'
import json
import os
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    root = json.load(stream)
runtime_model = sys.argv[2]
bot = root["bots"][0]
runtime = bot["runtime"]
values = [
    bot["source"], bot["name"], bot["summary"], str(runtime["relay_port"]),
    os.path.expanduser(runtime["claude_config_dir"]), os.path.expanduser(runtime["workspace"]),
    runtime_model, os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), runtime["system_prompt_md"]),
    runtime["permission_mode"],
]
if any("\x1f" in value or "\t" in value or "\n" in value or "\r" in value for value in values):
    raise SystemExit("profile values contain an unsupported separator")
print("\x1f".join(values))
PY
}

claude_profile_entity_id() {
    jq -r '.entity_id // "mock-user"' "$(claude_profile_manifest)"
}

claude_profile_entity_type() {
    jq -r '.entity_type // "staff"' "$(claude_profile_manifest)"
}

# Return the first Claude bot's comma-separated domains (from bots.json).
claude_profile_first_bot_domains() {
    jq -r '.bots[0].domains // empty' "$(claude_profile_manifest)" 2>/dev/null
}

# Return the first Claude bot's comma-separated skills (from bots.json).
claude_profile_first_bot_skills() {
    jq -r '.bots[0].skills // empty' "$(claude_profile_manifest)" 2>/dev/null
}
