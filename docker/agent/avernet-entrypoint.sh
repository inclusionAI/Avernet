#!/usr/bin/env bash
# docker/agent/avernet-entrypoint.sh
#
# Container entrypoint for Avernet Engine + OpenClaw image.
#
# Runtime model (mirrors ocb/dockers/arca-openclaw/entrypoint.sh):
#   - supervisord runs as PID 1 (started via exec)
#   - [program:engine]   autostart=false — started by start_service.sh (external)
#   - [program:openclaw]  autostart=false — started by engine on demand
#
# Flow:
#   1. Create runtime directories
#   2. Generate ~/.openclaw/openclaw.json from template + env vars (if not mounted)
#   3. exec supervisord (becomes PID 1)
#
# The platform invokes start_service.sh externally (docker exec) with
# --token/--client_id to save credentials and start the engine.

set -euo pipefail

export HOME="${HOME:-/home/admin}"
CONFIG_DIR="${HOME}/.openclaw"
CONFIG_FILE="${CONFIG_DIR}/openclaw.json"
WORKSPACE_DIR="${CONFIG_DIR}/workspace"
LOG_DIR="${HOME}/logs"
TEMPLATE_FILE="/opt/openclaw.json.template"

# --- 1. Create runtime directories

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

# --- 3. Generate openclaw.json from template if none mounted

if [ ! -f "${CONFIG_FILE}" ]; then
    echo "===> generating ${CONFIG_FILE} from template"

    # Run config generation as admin so the file is admin-owned directly.
    _gen_config() {
        cp "${TEMPLATE_FILE}" "${CONFIG_FILE}"

        _sub() {
            local val
            val="${!1:-UNSET}"
            val="${val//\//\\/}"
            sed -i "s/${2}/${val}/g" "${CONFIG_FILE}"
        }

        _sub OPENCLAW_OPENAI_BASE_URL  OPENCLAW_OPENAI_BASE_URL
        _sub OPENCLAW_OPENAI_API_KEY   OPENCLAW_OPENAI_API_KEY
        _sub OPENCLAW_OPENAI_MODEL_ID  OPENCLAW_OPENAI_MODEL_ID
        _sub OPENCLAW_OPENAI_MODEL_NAME OPENCLAW_OPENAI_MODEL_NAME
        _sub OPENCLAW_GATEWAY_TOKEN    OPENCLAW_GATEWAY_TOKEN
        _sub BCS_URL                   BCS_URL
        _sub BCS_BOT_ID                BCS_BOT_ID
        _sub BCS_BOT_NAME              BCS_BOT_NAME

        if [ "${OPENCLAW_GATEWAY_TOKEN:-UNSET}" = "UNSET" ]; then
            sed -i '/"auth": {/{N;s/"mode": "token", "token": "UNSET"//' "${CONFIG_FILE}" 2>/dev/null || true
        fi
    }
    export -f _gen_config
    export TEMPLATE_FILE CONFIG_FILE OPENCLAW_OPENAI_BASE_URL OPENCLAW_OPENAI_API_KEY \
           OPENCLAW_OPENAI_MODEL_ID OPENCLAW_OPENAI_MODEL_NAME OPENCLAW_GATEWAY_TOKEN \
           BCS_URL BCS_BOT_ID BCS_BOT_NAME
    su admin -s /bin/bash -c '_gen_config'

    echo "    config written"
else
    echo "===> using existing ${CONFIG_FILE} (mounted or pre-built)"
fi

# --- 4. exec supervisord — becomes PID 1
# Engine and openclaw are both autostart=false.
# The platform invokes start_service.sh externally (e.g. docker exec)
# with --token/--client_id to orchestrate pod startup.

echo "===> starting supervisord (engine + openclaw on demand via start_service.sh)"
exec /usr/local/bin/supervisord -n -c /etc/supervisor/supervisord.conf
