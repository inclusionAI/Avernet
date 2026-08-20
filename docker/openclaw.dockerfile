########## Avernet Engine + OpenClaw ##########
# Two-stage build producing an image that runs both the Python engine adapter
# and the OpenClaw gateway under supervisord.
#
# Reference: ocb/dockers/desktop-openclaw/Dockerfile
#
# Runtime model (mirrors OCB desktop-openclaw):
#   - supervisord is PID 1
#   - [program:engine]    autostart=true  — starts on container boot
#   - [program:openclaw]  autostart=false — started on demand by engine
#                        via `sudo supervisorctl start openclaw`
#
# Build args:
#   OPENCLAW_VERSION   npm version of openclaw (default 2026.6.1)
#   UV_VERSION         uv version for pin (default: latest)
#   USE_CN_MIRROR      set to "1" for China-friendly apt/npm/pip mirrors
#   NPM_STRICT_SSL     npm strict-ssl toggle (default true)

# ==================== Stage 1: Builder ====================
FROM node:22-bookworm-slim AS builder

ARG OPENCLAW_VERSION=2026.6.1
ARG USE_CN_MIRROR=
ARG NPM_STRICT_SSL=true

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/root \
    PATH=/usr/local/bin:/root/.local/bin:$PATH

WORKDIR /opt

# Install build + system dependencies in one layer.
RUN if [ "${USE_CN_MIRROR}" = "1" ]; then \
        sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
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
RUN if [ "${USE_CN_MIRROR}" = "1" ]; then \
        npm config set registry "https://registry.npmmirror.com"; \
    fi \
    && npm config set strict-ssl "${NPM_STRICT_SSL}" \
    && npm install -g "openclaw@${OPENCLAW_VERSION}"

# Install uv (Python package manager, standalone binary).
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && cp /root/.local/bin/uv /usr/local/bin/uv

# Build engine virtualenv from pyproject.toml.
COPY src/engine/ /opt/engine/
RUN uv venv --python 3 /opt/.venv \
    && if [ "${USE_CN_MIRROR}" = "1" ]; then \
           UV_INDEX_URL="https://mirrors.aliyun.com/pypi/simple" \
           uv pip install --python /opt/.venv/bin/python -r /opt/engine/pyproject.toml; \
       else \
           uv pip install --python /opt/.venv/bin/python -r /opt/engine/pyproject.toml; \
       fi \
    && if [ "${USE_CN_MIRROR}" = "1" ]; then \
           UV_INDEX_URL="https://mirrors.aliyun.com/pypi/simple" \
           uv pip install --python /opt/.venv/bin/python -e /opt/engine; \
       else \
           uv pip install --python /opt/.venv/bin/python -e /opt/engine; \
       fi \
    && find /opt/.venv -name "*.pyc" -delete \
    && find /opt/.venv -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true \
    && rm -rf /root/.cache/uv /root/.local/share/uv

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
    && mkdir -p /home/admin/.openclaw/workspace /home/admin/logs \
    && chown -R admin:admin /home/admin

# Create symlink for CA bundle path expected by run.sh / Node (RHEL-style path on Debian).
RUN ln -sf /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-bundle.crt

# ==================== Stage 2: Runtime ====================
FROM node:22-bookworm-slim AS runtime

ARG USE_CN_MIRROR=

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/home/admin \
    OPENCLAW_PORT=18789 \
    OPENCLAW_STATE_DIR=/home/admin/.openclaw \
    PATH=/usr/local/bin:/home/admin/.local/bin:$PATH \
    TZ=Asia/Shanghai

# Runtime deps only (no build tools).
RUN if [ "${USE_CN_MIRROR}" = "1" ]; then \
        sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        jq \
        lsof \
        procps \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Bring over installed openclaw from builder.
COPY --from=builder /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=builder /usr/local/bin/openclaw /usr/local/bin/openclaw

# Bring over the engine venv + source code.
COPY --from=builder /opt/.venv /opt/.venv
COPY --from=builder /opt/engine /opt/engine

# Bring over supervisor binaries.
COPY --from=builder /opt/supervisor-venv /opt/supervisor-venv
COPY --from=builder /usr/local/bin/supervisord /usr/local/bin/supervisord
COPY --from=builder /usr/local/bin/supervisorctl /usr/local/bin/supervisorctl

# Bring over uv for developer convenience.
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv

# CA bundle symlink for run.sh's hardcoded NODE_EXTRA_CA_CERTS path.
RUN ln -sf /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-bundle.crt

# Recreate admin user at runtime stage (uid/gid 10001).
RUN groupadd --gid 10001 admin 2>/dev/null || true \
    && useradd --uid 10001 --gid admin --create-home --shell /bin/bash admin 2>/dev/null || true \
    && echo 'admin ALL=(ALL) NOPASSWD: /usr/local/bin/supervisorctl *' > /etc/sudoers.d/admin-supervisorctl \
    && chmod 440 /etc/sudoers.d/admin-supervisorctl \
    && mkdir -p /home/admin/.openclaw/workspace \
               /home/admin/logs \
               /var/log/supervisor \
               /var/run \
    && chown -R admin:admin /home/admin

# Supervisor configuration: engine(autostart=true) + openclaw(autostart=false).
COPY docker/openclaw-supervisord.conf /etc/supervisor/supervisord.conf

# Entrypoint: generates ~/.openclaw/openclaw.json from env vars, then starts supervisord.
COPY docker/openclaw-entrypoint.sh /usr/local/bin/openclaw-entrypoint
RUN chmod +x /usr/local/bin/openclaw-entrypoint

EXPOSE 20003 18789

HEALTHCHECK --interval=10s --timeout=5s --start-period=120s --retries=6 \
    CMD curl -fsS "http://127.0.0.1:20003/health" >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/openclaw-entrypoint"]
