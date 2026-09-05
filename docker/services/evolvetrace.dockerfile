# Evolvetrace Runtime Image for ACK / Aliyun Deployment
#
# Build context: repository root (selectively copies only src/evolverun/evolvetrace).
# This keeps internal modules and secrets out of the evolvetrace image.
#
# Usage from repo root:
#   docker build -f docker/evolvetrace.Dockerfile -t evolvetrace:local .

# --- Build stage: install deps, build Vite frontend, compile TS server ---
FROM node:22-bookworm AS builder

# Native dependencies such as better-sqlite3 may compile during npm install.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 build-essential \
    && rm -rf /var/lib/apt/lists/*

ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org
ARG NODEJS_DIST_URL=https://nodejs.org/dist
ENV npm_config_registry=${NPM_CONFIG_REGISTRY} \
    npm_config_disturl=${NODEJS_DIST_URL} \
    npm_config_python=/usr/bin/python3

WORKDIR /build

# Copy package metadata first for layer caching.
COPY src/evolverun/evolvetrace/package.json src/evolverun/evolvetrace/package-lock.json ./
RUN npm ci --no-audit --no-fund

# Copy source and build both frontend and server.
COPY src/evolverun/evolvetrace/ ./
RUN npm run build && npm run build:server \
    && npm prune --omit=dev

# --- Runtime stage ---
FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    EVOLVETRACE_ENV=prod \
    PORT=3001 \
    DATABASE_MODE=mysql

# Create non-root runtime user.
RUN groupadd -r appuser && useradd -r -g appuser -s /bin/bash appuser

# Network diagnostics (ping) for in-container troubleshooting; the slim base
# ships without it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only production artifacts and runtime files.
COPY --from=builder --chown=appuser:appuser /build/dist ./dist
COPY --from=builder --chown=appuser:appuser /build/dist-server ./dist-server
COPY --from=builder --chown=appuser:appuser /build/configs ./configs
COPY --from=builder --chown=appuser:appuser /build/scripts ./scripts
COPY --from=builder --chown=appuser:appuser /build/package.json ./package.json
COPY --from=builder --chown=appuser:appuser /build/package-lock.json ./package-lock.json
COPY --from=builder --chown=appuser:appuser /build/node_modules ./node_modules

# The service runs as appuser (non-root). Root stays available for debugging —
# it is merely password-locked, and container exec needs no password:
#   kubectl exec -it <pod> -u 0 -- bash    (then apt-get install ...)
# Root password stays unset on purpose: no secret ships in image layers.
USER appuser

EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://localhost:3001/health').then(r => r.ok || process.exit(1), () => process.exit(1))" || exit 1

CMD ["node", "dist-server/index.js"]
