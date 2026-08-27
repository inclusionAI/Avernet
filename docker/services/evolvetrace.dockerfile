# Evolvetrace Runtime Image for ACK / Aliyun Deployment
#
# Build context: repository root (selectively copies only src/evolverun/evolvetrace).
# This keeps internal modules and secrets out of the evolvetrace image.
#
# Usage from repo root:
#   docker build -f docker/evolvetrace.Dockerfile -t evolvetrace:local .

# --- Build stage: install deps, build Vite frontend, compile TS server ---
FROM node:20-bookworm AS builder

# Allow optional npm registry mirror (e.g. China CI builds).
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org
ENV npm_config_registry=${NPM_CONFIG_REGISTRY}

WORKDIR /build

# Copy package metadata first for layer caching.
COPY src/evolverun/evolvetrace/package.json src/evolverun/evolvetrace/package-lock.json ./
RUN npm config set registry "${npm_config_registry}" \
    && npm config set legacy-peer-deps true \
    && npm ci --no-audit --no-fund

# Copy source and build both frontend and server.
COPY src/evolverun/evolvetrace/ ./
RUN npm run build && npm run build:server

# --- Runtime stage ---
FROM node:20-bookworm-slim

# Allow optional npm registry mirror (must match builder stage).
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org
ENV npm_config_registry=${NPM_CONFIG_REGISTRY}

ENV NODE_ENV=production \
    EVOLVETRACE_ENV=prod \
    PORT=3001 \
    DATABASE_MODE=mysql

# Create non-root runtime user.
RUN groupadd -r appuser && useradd -r -g appuser -s /bin/bash appuser

WORKDIR /app

# Copy only production artifacts and runtime files.
COPY --from=builder --chown=appuser:appuser /build/dist ./dist
COPY --from=builder --chown=appuser:appuser /build/dist-server ./dist-server
COPY --from=builder --chown=appuser:appuser /build/configs ./configs
COPY --from=builder --chown=appuser:appuser /build/scripts ./scripts
COPY --from=builder --chown=appuser:appuser /build/package.json ./package.json
COPY --from=builder --chown=appuser:appuser /build/package-lock.json ./package-lock.json

# Install production dependencies only (mysql2 etc.).
RUN npm config set registry "${npm_config_registry}" \
    && npm config set legacy-peer-deps true \
    && npm ci --omit=dev --no-audit --no-fund \
    && rm -rf ~/.npm

USER appuser

EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://localhost:3001/health').then(r => r.ok || process.exit(1), () => process.exit(1))" || exit 1

CMD ["node", "dist-server/index.js"]
