########## Avernet Engine + OpenClaw ##########
# Two-stage build producing an image that runs both the Python engine adapter
# and the OpenClaw gateway under supervisord.
#
# Reference: ocb/dockers/arca-openclaw/Dockerfile
#
# Runtime model (mirrors OCB arca-openclaw):
#   - supervisord is PID 1
#   - [program:engine]    autostart=false — started by start_service.sh
#   - [program:openclaw]  autostart=false — started on demand by engine
#                        via `sudo supervisorctl start openclaw`
#
# Pod startup flow:
#   1. entrypoint.sh: pre-init (directories, config), then exec supervisord
#   2. start_service.sh (background): save credentials → start engine
#      via supervisorctl → poll /health → write ready marker
#
# Build args:
#   OPENCLAW_VERSION   npm version of openclaw (default 2026.6.1)
#   UV_VERSION         uv version for pin (default: latest)
#   NPM_STRICT_SSL     npm strict-ssl toggle (default true)

# ==================== Stage 1: Builder ====================
FROM node:22-bookworm-slim AS builder

ARG OPENCLAW_VERSION=2026.5.12
ARG NPM_STRICT_SSL=true

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/root \
    PATH=/usr/local/bin:/root/.local/bin:$PATH

WORKDIR /opt

# Install build + system dependencies in one layer.
RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        jq \
        lsof \
        make \
        procps \
        python3 \
        python3-pip \
        python3-venv \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Install OpenClaw via npm (global).
RUN npm config set registry "https://registry.npmmirror.com" \
    && npm config set strict-ssl "${NPM_STRICT_SSL}" \
    && npm install -g "openclaw@${OPENCLAW_VERSION}"

# Install uv via pip (Aliyun mirror — astral.sh is unreachable in CN build envs).
# --break-system-packages: required by PEP 668 on Debian 12 system Python.
ARG UV_VERSION=
RUN pip3 install --no-cache-dir --break-system-packages \
        -i https://mirrors.aliyun.com/pypi/simple \
        "uv${UV_VERSION:+==${UV_VERSION}}" \
    && which uv && uv --version

# Build engine virtualenv from pyproject.toml.
COPY src/engine/ /opt/engine/
RUN uv venv --python 3 /opt/.venv \
    && UV_INDEX_URL="https://mirrors.aliyun.com/pypi/simple" \
       uv pip install --python /opt/.venv/bin/python -r /opt/engine/pyproject.toml \
    && UV_INDEX_URL="https://mirrors.aliyun.com/pypi/simple" \
       uv pip install --python /opt/.venv/bin/python -e /opt/engine \
    && find /opt/.venv -name "*.pyc" -delete \
    && find /opt/.venv -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true \
    && rm -rf /root/.cache/uv /root/.local/share/uv

# Build the openclaw-channel-bcn plugin (BCS WebSocket channel).
# Mirrors Dockerfile.ocb: npm install → build → prune devDeps.
COPY src/bcs/crates/plugins/openclaw-channel-bcn/ /tmp/openclaw-channel-bcn/
RUN cd /tmp/openclaw-channel-bcn \
    && npm install \
    && npm run build \
    && npm prune --omit=dev \
    && mkdir -p /opt/openclawExt/openclaw-channel-bcn \
    && cp -R dist node_modules package.json openclaw.plugin.json \
           /opt/openclawExt/openclaw-channel-bcn/ \
    && rm -rf /tmp/openclaw-channel-bcn

# Install supervisor in an isolated venv (avoids conflicts with engine site-packages).
RUN python3 -m venv /opt/supervisor-venv \
    && /opt/supervisor-venv/bin/pip install --no-cache-dir supervisor \
    && ln -sf /opt/supervisor-venv/bin/supervisord /usr/local/bin/supervisord \
    && ln -sf /opt/supervisor-venv/bin/supervisorctl /usr/local/bin/supervisorctl \
    && mkdir -p /var/log/supervisor /var/run

# Create admin user (uid/gid 10001) for running engine + openclaw.
RUN groupadd --gid 10001 admin 2>/dev/null || true \
    && useradd --uid 10001 --gid admin --create-home --shell /bin/bash admin 2>/dev/null || true \
    && echo 'admin:*' | chpasswd -e \
    && echo 'admin ALL=(ALL) NOPASSWD: /usr/local/bin/supervisorctl *' > /etc/sudoers.d/admin-supervisorctl \
    && chmod 440 /etc/sudoers.d/admin-supervisorctl \
    && su admin -s /bin/bash -c 'mkdir -p /home/admin/.openclaw/workspace /home/admin/logs'

# Create symlink for CA bundle path expected by run.sh / Node (RHEL-style path on Debian).
RUN ln -sf /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-bundle.crt

# ==================== Stage 2: Runtime ====================
FROM node:22-bookworm-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/home/admin \
    OPENCLAW_PORT=18789 \
    OPENCLAW_GATEWAY_TOKEN=ack-openclaw \
    OPENCLAW_STATE_DIR=/home/admin/.openclaw \
    PATH=/usr/local/bin:/home/admin/.local/bin:$PATH \
    TZ=Asia/Shanghai

# Runtime deps (python3 needed by supervisor venv symlink).
RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        jq \
        lsof \
        procps \
        python3 \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Bring over installed openclaw from builder.
COPY --from=builder /usr/local/lib/node_modules /usr/local/lib/node_modules
# Recreate npm bin symlink: COPY --from resolves symlinks to files,
# which breaks the script's __dirname-based dist/ path resolution.
RUN BIN_REL=$(node -e "\
  const p = require('/usr/local/lib/node_modules/openclaw/package.json'); \
  const b = p.bin || {}; \
  const t = typeof b === 'string' ? b : (b.openclaw || Object.values(b)[0]); \
  console.log(t)") \
    && ln -sf "/usr/local/lib/node_modules/openclaw/${BIN_REL}" \
              /usr/local/bin/openclaw \
    && chmod +x /usr/local/bin/openclaw

# Bring over the engine venv + source code.
COPY --from=builder /opt/.venv /opt/.venv
COPY --from=builder /opt/engine /opt/engine

# Bring over supervisor binaries.
COPY --from=builder /opt/supervisor-venv /opt/supervisor-venv
COPY --from=builder /usr/local/bin/supervisord /usr/local/bin/supervisord
COPY --from=builder /usr/local/bin/supervisorctl /usr/local/bin/supervisorctl

# Bring over uv for developer convenience.
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv

# Bring over the openclaw-channel-bcn plugin and link it into openclaw extensions.
COPY --from=builder /opt/openclawExt/openclaw-channel-bcn /opt/openclawExt/openclaw-channel-bcn

# CA bundle symlink for run.sh's hardcoded NODE_EXTRA_CA_CERTS path.
# Optionally trust the avernet-sidecar MITM CA (generated by gen-mitm-ca.sh):
# COPY with wildcard — if mitm-ca.crt is absent, the build still succeeds.
COPY docker/agent/avernet-sidecar/mitm-ca.crt* /tmp/
RUN if [ -f /tmp/mitm-ca.crt ]; then \
        cp /tmp/mitm-ca.crt /usr/local/share/ca-certificates/avernet-sidecar-mitm-ca.crt; \
    fi \
    && update-ca-certificates \
    && ln -sf /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-bundle.crt

# Recreate admin user at runtime stage (uid/gid 10001).
RUN groupadd --gid 10001 admin 2>/dev/null || true \
    && useradd --uid 10001 --gid admin --create-home --shell /bin/bash admin 2>/dev/null || true \
    && echo 'admin ALL=(ALL) NOPASSWD: /usr/local/bin/supervisorctl *' > /etc/sudoers.d/admin-supervisorctl \
    && chmod 440 /etc/sudoers.d/admin-supervisorctl \
    && mkdir -p /var/log/supervisor /var/run/agentclaw \
    && chown admin:admin /var/run/agentclaw \
    && su admin -s /bin/bash -c 'mkdir -p /home/admin/.openclaw/workspace /home/admin/.openclaw/extensions /home/admin/logs' \
    && ln -sfn /opt/openclawExt/openclaw-channel-bcn \
               /home/admin/.openclaw/extensions/openclaw-channel-bcn

# Supervisor configuration: engine(autostart=false) + openclaw(autostart=false).
COPY docker/agent/avernet-supervisord.conf /etc/supervisor/supervisord.conf

# OpenClaw default config template (env-var placeholders substituted at runtime).
COPY docker/agent/openclaw.json /opt/openclaw.json.template

# Shared utility functions (logging, helpers).
COPY docker/agent/util.sh /usr/local/bin/util.sh

# Simplified pod startup script (starts engine, waits for health).
COPY docker/agent/start_service.sh /usr/local/bin/start_service.sh

# Entrypoint: pre-init, config generation from template, then execs supervisord.
COPY docker/agent/avernet-entrypoint.sh /usr/local/bin/avernet-entrypoint
RUN chmod +x /usr/local/bin/avernet-entrypoint \
             /usr/local/bin/start_service.sh \
             /usr/local/bin/util.sh

EXPOSE 20003 18789

HEALTHCHECK --interval=10s --timeout=5s --start-period=120s --retries=6 \
    CMD curl -fsS "http://127.0.0.1:20003/health" >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/avernet-entrypoint"]
