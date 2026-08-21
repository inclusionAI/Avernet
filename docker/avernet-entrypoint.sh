#!/usr/bin/env bash
# docker/avernet-entrypoint.sh
#
# Container entrypoint for Avernet Engine + OpenClaw image.
#
# Runtime model (mirrors ocb/dockers/arca-openclaw/entrypoint.sh):
#   - supervisord runs as PID 1 (started via exec)
#   - [program:engine]   autostart=false — started by start_service.sh
#   - [program:openclaw]  autostart=false — started by engine on demand
#
# Flow:
#   1. Create runtime directories
#   2. Generate ~/.openclaw/openclaw.json from env vars (if not mounted)
#   3. Schedule start_service.sh as a background subshell
#   4. exec supervisord (becomes PID 1)

set -euo pipefail

export HOME="${HOME:-/home/admin}"
CONFIG_DIR="${HOME}/.openclaw"
CONFIG_FILE="${CONFIG_DIR}/openclaw.json"
WORKSPACE_DIR="${CONFIG_DIR}/workspace"
LOG_DIR="${HOME}/logs"

SCRIPT_DIR="/usr/local/bin"

# --- 1. Create runtime directories ——

mkdir -p "${CONFIG_DIR}/extensions" "${WORKSPACE_DIR}" "${LOG_DIR}"
mkdir -p /var/run/agentclaw /var/log/supervisor /var/run

# --- 2. Verify build artifacts

if [ ! -f "/opt/.venv/bin/activate" ]; then
    echo "ERROR: engine venv not found at /opt/.venv" >&2
    echo "Please rebuild the Docker image" >&2
    exit 1
fi
if [ ! -x "/usr/local/bin/supervisord" ]; then
    echo "ERROR: supervisord not found at /usr/local/bin/supervisord" >&2
    echo "Please rebuild the Docker image" >&2
    exit 1
fi

if command -v openclaw &>/dev/null; then
    echo "===> OpenClaw: $(openclaw --version 2>&1 | head -1 || echo 'unknown')"
else
    echo "WARNING: OpenClaw not found in PATH" >&2
fi

echo "===> Environment:"
echo "     Engine:   /opt/engine (port 20003)"
echo "     OpenClaw: ${CONFIG_DIR} (port ${OPENCLAW_PORT:-18789})"
echo "     Logs:     ${LOG_DIR}"

# --- 3. Generate default openclaw.json if none exists (user may mount their own).

if [ ! -f "${CONFIG_FILE}" ]; then
    echo "===> generating default ${CONFIG_FILE}"
    node -e '
    const fs = require("fs");

    const port       = process.env.OPENCLAW_PORT || "18789";
    const providerId = process.env.OPENCLAW_OPENAI_PROVIDER_ID || "openai-compatible";
    const baseUrl    = process.env.OPENCLAW_OPENAI_BASE_URL || "";
    const apiKey     = process.env.OPENCLAW_OPENAI_API_KEY || "";
    const modelId    = process.env.OPENCLAW_OPENAI_MODEL_ID || "";
    const modelName  = process.env.OPENCLAW_OPENAI_MODEL_NAME || modelId;
    const modelApi   = process.env.OPENCLAW_OPENAI_MODEL_API || "openai-completions";
    const gwToken    = process.env.OPENCLAW_GATEWAY_TOKEN || "";
    const bcsUrl     = process.env.BCS_URL || "";
    const workspace  = process.env.HOME + "/.openclaw/workspace";

    const providers = {};
    if (baseUrl || apiKey || modelId) {
      providers[providerId] = {
        baseUrl, apiKey,
        auth: "api-key",
        api: modelApi,
        models: [{
          id: modelId,
          name: modelName,
          api: modelApi,
          reasoning: true,
          input: ["text"],
          contextWindow: 100000,
          maxTokens: 65536
        }]
      };
    }

    const agentDefaults = {
      workspace,
      compaction: { mode: "safeguard" },
      maxConcurrent: 4,
      subagents: { maxConcurrent: 8 }
    };
    if (modelId) {
      agentDefaults.model = { primary: providerId + "/" + modelId };
      agentDefaults.models = {};
      agentDefaults.models[providerId + "/" + modelId] = { alias: modelName };
    }

    const channels = {};
    if (bcsUrl) {
      const csv = (s) => s ? s.split(",").map(x => x.trim()).filter(Boolean) : [];
      channels.bcs = {
        enabled: true,
        bcsUrl,
        botId:     process.env.BCS_BOT_ID || "openclaw-bot",
        botName:   process.env.BCS_BOT_NAME || process.env.BCS_BOT_ID || "openclaw-bot",
        capabilities: {
          summary: process.env.BCS_BOT_SUMMARY || "OpenClaw bot",
          domains: csv(process.env.BCS_BOT_DOMAINS || "general"),
          skills:  csv(process.env.BCS_BOT_SKILLS  || "general"),
          scopes:  csv(process.env.BCS_BOT_SCOPES  || "production")
        },
        heartbeatIntervalMs: 60000,
        reconnectIntervalMs: 5000,
        connectionTimeoutMs: 30000
      };
    }

    const auth = gwToken
      ? { mode: "token", token: gwToken }
      : {};

    const config = {
      meta: { lastTouchedVersion: "2026.6.1" },
      models: {
        mode: "merge",
        providers
      },
      agents: {
        defaults: agentDefaults,
        list: [{ id: "main" }]
      },
      tools: { profile: "coding" },
      messages: { ackReactionScope: "group-mentions" },
      commands: {
        native: "auto",
        nativeSkills: "auto",
        restart: true,
        ownerDisplay: "raw"
      },
      session: { dmScope: "per-channel-peer" },
      hooks: {
        internal: {
          enabled: true,
          entries: { "boot-md": { enabled: true } }
        }
      },
      gateway: {
        port: parseInt(port, 10),
        mode: "local",
        bind: "0.0.0.0",
        controlUi: { dangerouslyDisableDeviceAuth: true },
        auth,
        tailscale: { mode: "off", resetOnExit: false },
        nodes: {
          denyCommands: [
            "camera.snap", "camera.clip", "screen.record",
            "calendar.add", "contacts.add", "reminders.add"
          ]
        }
      }
    };

    if (Object.keys(channels).length > 0) {
      config.channels = channels;
    }

    fs.writeFileSync(process.env.HOME + "/.openclaw/openclaw.json",
                     JSON.stringify(config, null, 2) + "\n");
    console.log("    config written");
    '
    chown admin:admin "${CONFIG_FILE}" 2>/dev/null || true
else
    echo "===> using existing ${CONFIG_FILE} (mounted or pre-built)"
fi

# --- 4. Schedule start_service.sh as a background subshell.
# It waits for the supervisord socket, then starts the engine program
# and polls its /health endpoint.  Running it in the background lets
# supervisord become PID 1 and receive SIGTERM directly for graceful
# shutdown.

ENGINE="${ENGINE:-openclaw}"
TOKEN="${TOKEN:-}"
CLIENT_ID="${CLIENT_ID:-}"
BOT_ID="${BOT_ID:-}"
STAGE="${STAGE:-}"
OWNER_ID="${OWNER_ID:-}"

START_ARGS=()
[ -n "$TOKEN" ]      && START_ARGS+=(--token "$TOKEN")
[ -n "$CLIENT_ID" ]  && START_ARGS+=(--client_id "$CLIENT_ID")
[ -n "$BOT_ID" ]     && START_ARGS+=(--bot_id "$BOT_ID")
[ -n "$STAGE" ]      && START_ARGS+=(--stage "$STAGE")
[ -n "$OWNER_ID" ]   && START_ARGS+=(--owner_id "$OWNER_ID")
START_ARGS+=(--engine "$ENGINE")

echo "===> scheduling start_service.sh in background"
(
    # Wait for supervisord socket to appear
    for _ in $(seq 1 30); do
        [ -S /var/run/supervisor.sock ] && break
        sleep 1
    done
    if [ -S /var/run/supervisor.sock ]; then
        bash "${SCRIPT_DIR}/start_service.sh" "${START_ARGS[@]}" \
            2>&1 | tee -a "${LOG_DIR}/start_service.log"
    else
        echo "[entrypoint] ERROR: supervisord socket never appeared, skipping start_service.sh" >&2
    fi
) &

# --- 5. exec supervisord — becomes PID 1
echo "===> starting supervisord (engine + openclaw on demand)"
exec /usr/local/bin/supervisord -n -c /etc/supervisor/supervisord.conf
