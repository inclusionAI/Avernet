#!/usr/bin/env bash
# docker/openclaw-entrypoint.sh
#
# Container entrypoint for Avernet Engine + OpenClaw image.
#
# Runtime model (mirrors ocb/dockers/desktop-openclaw/entrypoint.sh):
#   - supervisord is started as PID 1
#   - [program:engine]   autostart=true  — starts on container boot
#   - [program:openclaw]  autostart=false — started by engine on demand
#     via `sudo supervisorctl start openclaw`
#
# This script:
#   1. Creates runtime directories for openclaw and logs
#   2. Generates ~/.openclaw/openclaw.json from environment variables
#      (unless a config was already mounted)
#   3. Launches supervisord

set -euo pipefail

export HOME="${HOME:-/home/admin}"
CONFIG_DIR="${HOME}/.openclaw"
CONFIG_FILE="${CONFIG_DIR}/openclaw.json"
WORKSPACE_DIR="${CONFIG_DIR}/workspace"
LOG_DIR="${HOME}/logs"

# --- 1. Create runtime directories —-

mkdir -p "${CONFIG_DIR}/extensions" "${WORKSPACE_DIR}" "${LOG_DIR}"

# --- 2. Check that the engine venv + supervisor exist
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

# --- Check openclaw is installed
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

    // --- Build model providers (only when baseUrl or apiKey or modelId is set)
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

    // --- Build agent defaults
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

    // --- Build channels (bcs only when BCS_URL is set)
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

    // --- Build gateway auth
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

    // Add channels if non-empty
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

echo "===> starting supervisord (engine=autostart, openclaw=on-demand)"
exec /usr/local/bin/supervisord -n -c /etc/supervisor/supervisord.conf
