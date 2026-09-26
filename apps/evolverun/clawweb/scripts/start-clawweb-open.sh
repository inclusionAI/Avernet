#!/usr/bin/env bash
set -euo pipefail

workspace="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
avernet_root="$(cd "${workspace}/../../.." && pwd -P)"
bot_source=""
config=""
openclaw_home="${HOME}/.openclaw"
backend_db="${avernet_root}/scripts/.dependencies/data/backend.db"
data_directory=""
user_id=""
model=""
port="5173"

usage() {
  cat <<EOF
Usage:
  bash $0 [--bot-source auto|openclaw|singlebox] [options]
  bash $0 --config /absolute/path/clawweb.local.yaml

Bot sources:
  auto       Reuse an existing Avernet Singlebox database when present;
             otherwise use the current user's ~/.openclaw (default).
  openclaw   Use one local OpenClaw profile directly; does not start Avernet Singlebox.
  singlebox  Reuse personal Bots already created by Avernet Singlebox; does not start it.

Options:
  --openclaw-home DIR  OpenClaw state/config directory (default: ~/.openclaw)
  --backend-db FILE    Avernet Backend SQLite database
  --data-dir DIR       ClawWeb local task/artifact directory
  --user-id ID         Local owner ID
  --model NAME         Initial provider/model; defaults to the OpenClaw profile model
  --port PORT          ClawWeb port (default: 5173)
  --config FILE        Use an existing YAML/JSON config and skip source discovery
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bot-source) bot_source="${2:-}"; shift 2 ;;
    --openclaw-home) openclaw_home="${2:-}"; shift 2 ;;
    --backend-db) backend_db="${2:-}"; shift 2 ;;
    --data-dir) data_directory="${2:-}"; shift 2 ;;
    --user-id) user_id="${2:-}"; shift 2 ;;
    --model) model="${2:-}"; shift 2 ;;
    --port) port="${2:-}"; shift 2 ;;
    --config) config="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -n "$config" ]]; then
  [[ -z "$bot_source" ]] || { echo "--config cannot be combined with --bot-source" >&2; exit 2; }
  exec bash "${workspace}/scripts/start-clawevolve-singlebox.sh" --config "$config"
fi

if [[ -z "$bot_source" ]]; then
  if [[ -t 0 ]]; then
    cat <<'EOF'
请选择 ClawWeb 使用的 Bot 来源：

1. 自动检测（推荐）  有 Avernet Singlebox 数据时复用，否则使用 ~/.openclaw
2. 本地 OpenClaw      直接使用 ~/.openclaw，不启动 Singlebox
3. Avernet Singlebox  复用已创建的个人 Bot，不启动 Singlebox
EOF
    read -r -p "请输入选项 [1]: " choice
    case "${choice:-1}" in
      1) bot_source="auto" ;;
      2) bot_source="openclaw" ;;
      3) bot_source="singlebox" ;;
      *) echo "无效选项：${choice}" >&2; exit 2 ;;
    esac
  else
    # CI and redirected invocations must remain deterministic and never block.
    bot_source="auto"
  fi
fi

case "$bot_source" in
  auto)
    if [[ -f "$backend_db" ]]; then bot_source="singlebox"; else bot_source="openclaw"; fi
    ;;
  openclaw|singlebox) ;;
  *) echo "--bot-source must be auto, openclaw, or singlebox" >&2; exit 2 ;;
esac

if [[ -z "$user_id" ]]; then
  if [[ "$bot_source" == "singlebox" ]]; then user_id="mock-user"; else user_id="${USER:-local-user}"; fi
fi
if [[ -z "$data_directory" ]]; then
  if [[ "$bot_source" == "singlebox" ]]; then
    # Preserve the existing ClawWeb Singlebox task/Bench/Pack history.
    data_directory="${HOME}/.local/share/clawweb-singlebox"
  else
    # Direct ~/.openclaw mode owns an independent local task catalog.
    data_directory="${HOME}/.local/share/clawweb-open"
  fi
fi

if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $port is already in use. Reuse the running ClawWeb or select another port with --port." >&2
  exit 1
fi

mkdir -p "$data_directory"
runtime_config="${data_directory}/clawweb.runtime.json"
skills_root="${avernet_root}/src/evolverun/clawweb-skills/clawevolve-skills"

if [[ -z "$model" && -f "$openclaw_home/openclaw.json" ]]; then
  model="$(node -e 'const fs=require("fs"); const j=JSON.parse(fs.readFileSync(process.argv[1],"utf8")); const m=j?.agents?.defaults?.model; process.stdout.write(typeof m==="string"?m:(m?.primary??""));' "$openclaw_home/openclaw.json")"
fi
[[ -n "$model" ]] || {
  echo "No model found. Configure agents.defaults.model.primary in $openclaw_home/openclaw.json or pass --model provider/model." >&2
  exit 1
}

if [[ "$bot_source" == "openclaw" ]]; then
  [[ -f "$openclaw_home/openclaw.json" && -d "$openclaw_home/workspace" ]] || {
    echo "OpenClaw home requires openclaw.json and workspace/: $openclaw_home" >&2
    exit 1
  }
  node - "$runtime_config" "$user_id" "$model" "$data_directory" "$port" "$skills_root" "$openclaw_home" <<'NODE'
const fs = require("fs");
const [file, userId, model, dataDirectory, port, skillsRoot, openclawHome] = process.argv.slice(2);
fs.writeFileSync(file, JSON.stringify({ botSource: "openclaw", userId, model, dataDirectory,
  port: Number(port), skillsRoot, openclawHome, localBotId: "local-openclaw", localBotName: "Local OpenClaw" }, null, 2) + "\n", { mode: 0o600 });
NODE
else
  [[ -f "$backend_db" ]] || { echo "Avernet Backend database not found: $backend_db" >&2; exit 1; }
  node - "$runtime_config" "$user_id" "$model" "$data_directory" "$port" "$skills_root" "$backend_db" <<'NODE'
const fs = require("fs");
const [file, userId, model, dataDirectory, port, skillsRoot, backendDb] = process.argv.slice(2);
fs.writeFileSync(file, JSON.stringify({ botSource: "singlebox", userId, model, dataDirectory,
  port: Number(port), skillsRoot, backendDb }, null, 2) + "\n", { mode: 0o600 });
NODE
fi

echo "[clawweb] bot source: $bot_source"
echo "[clawweb] runtime config: $runtime_config"
exec bash "${workspace}/scripts/start-clawevolve-singlebox.sh" --config "$runtime_config"
