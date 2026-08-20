########## OpenClaw Gateway ##########
FROM node:22-bookworm-slim

# Pin openclaw version, e.g. --build-arg OPENCLAW_VERSION=2026.6.1
ARG OPENCLAW_VERSION=2026.6.1

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/home/admin \
    OPENCLAW_PORT=18789 \
    PATH=/usr/local/bin:$PATH

WORKDIR /app

# Aliyun mirrors for apt + npm; install supervisor for process management.
# Create admin user (uid/gid 10001) to run openclaw.
RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl supervisor \
    && rm -rf /var/lib/apt/lists/* \
    && npm config set registry https://registry.npmmirror.com \
    && npm install -g "openclaw@${OPENCLAW_VERSION}" \
    && groupadd --gid 10001 admin \
    && useradd --uid 10001 --gid admin --create-home --shell /bin/bash admin \
    && mkdir -p /home/admin/.openclaw/extensions \
               /home/admin/.openclaw/workspace \
               /home/admin/logs \
    && chown -R admin:admin /home/admin /app

# Entrypoint: generates default openclaw.json from env vars, then starts supervisor.
COPY docker/openclaw-entrypoint.sh /usr/local/bin/openclaw-entrypoint
RUN chmod +x /usr/local/bin/openclaw-entrypoint

# Supervisor configuration: run openclaw gateway as admin user.
RUN cat > /etc/supervisor/conf.d/openclaw.conf << 'EOF'
[program:openclaw]
command=openclaw gateway run --port 18789
directory=/app
user=admin
environment=HOME="/home/admin"
autostart=true
autorestart=true
startsecs=5
startretries=3
stdout_logfile=/home/admin/logs/openclaw.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
stderr_logfile=/home/admin/logs/openclaw.err
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=3
EOF

EXPOSE 18789

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${OPENCLAW_PORT:-18789}/health" >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/openclaw-entrypoint"]
