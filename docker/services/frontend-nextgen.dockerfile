# syntax=docker/dockerfile:1.7

########## Builder ##########
FROM node:22-bookworm-slim AS build

WORKDIR /app

# Build context is the repository root, as required by docker/build-image.sh.
COPY src/frontend-nextgen/package.json src/frontend-nextgen/package-lock.json ./
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN --mount=type=cache,id=frontend-nextgen-npm,target=/root/.npm,sharing=locked \
    npm ci \
      --registry="${NPM_REGISTRY}" \
      --replace-registry-host=always \
      --prefer-offline \
      --no-audit \
      --fund=false \
      --fetch-retries=5 \
      --fetch-retry-mintimeout=20000 \
      --fetch-retry-maxtimeout=120000 \
      --fetch-timeout=300000

COPY src/frontend-nextgen/ ./
RUN npm run build

########## Runtime ##########
FROM nginx:1.27-alpine

# Network diagnostics (ping) for in-container troubleshooting. Alpine's
# BusyBox ping lacks options the full iputils build has; iputils is the
# distro's full-featured package.
RUN apk add --no-cache iputils

COPY src/frontend-nextgen/deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY src/frontend-nextgen/deploy/docker-entrypoint.sh /docker-entrypoint.d/20-validate-teamclaw-env.sh
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
