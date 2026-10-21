# syntax=docker/dockerfile:1.7

########## Builder ##########
FROM node:22-bookworm-slim AS build

# Monorepo 布局镜像内同构（review R6-1）：/repo 下保持源码仓目录几何——
# config/tsconfig 里的 '@avernet/a2a-chat-client' alias 用路径上引（../../../
# 和 ../../）跨出 app 目录指 packages/，若镜像把 app 摊平到 /app 而包复制在
# /app/packages，alias 会解析到差一层的不存在路径（构建必然失败）。∴ app
# 留在其完整层级 apps/frontend-nextgen/，packages/ 与之间距与源码仓一致。
WORKDIR /repo

COPY packages/a2a-chat-client/ ./packages/a2a-chat-client/
COPY apps/frontend-nextgen/package.json apps/frontend-nextgen/package-lock.json ./apps/frontend-nextgen/
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN --mount=type=cache,id=frontend-nextgen-npm,target=/root/.npm,sharing=locked \
    cd apps/frontend-nextgen && npm ci \
      --registry="${NPM_REGISTRY}" \
      --replace-registry-host=always \
      --prefer-offline \
      --no-audit \
      --fund=false \
      --fetch-retries=5 \
      --fetch-retry-mintimeout=20000 \
      --fetch-retry-maxtimeout=120000 \
      --fetch-timeout=300000

COPY apps/frontend-nextgen/ ./apps/frontend-nextgen/
RUN cd apps/frontend-nextgen && npm run build

########## Runtime ##########
FROM nginx:1.27-alpine

# Network diagnostics (ping) for in-container troubleshooting. Alpine's
# BusyBox ping lacks options the full iputils build has; iputils is the
# distro's full-featured package.
RUN apk add --no-cache iputils

COPY apps/frontend-nextgen/deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY apps/frontend-nextgen/deploy/docker-entrypoint.sh /docker-entrypoint.d/20-validate-teamclaw-env.sh
COPY --from=build /repo/apps/frontend-nextgen/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
