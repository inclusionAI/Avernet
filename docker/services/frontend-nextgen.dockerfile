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
ARG A2A_CLIENT_DEFAULT=false
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
ENV A2A_CLIENT_DEFAULT=${A2A_CLIENT_DEFAULT}
RUN cd apps/frontend-nextgen && npm run build

########## Runtime ##########
FROM nginx:1.27-alpine

# Network diagnostics (ping) for in-container troubleshooting. Alpine's
# BusyBox ping lacks options the full iputils build has; iputils is the
# distro's full-featured package.
RUN apk add --no-cache iputils

# full 模板保持 envsubst 输入位；BCS-only profile 模板放到不同的只读配置目录，
# 由 docker-entrypoint 按 FRONTEND_API_PROFILE 选择性拷入（与
# apps/frontend-nextgen/Dockerfile 的 COPY 三件套保持一致）。
COPY apps/frontend-nextgen/deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY apps/frontend-nextgen/deploy/nginx/default.bcs-only.conf.template /etc/nginx/template-profiles/default.bcs-only.conf.template
# 校验/换面脚本必须以 10-* 前缀先于官方 20-envsubst-on-templates.sh 运行，
# 否则 bcs-only 无法在标准变量替换前把 profile 模板放到 /etc/nginx/templates。
COPY apps/frontend-nextgen/deploy/docker-entrypoint.sh /docker-entrypoint.d/10-teamclaw-api-profile.sh
COPY --from=build /repo/apps/frontend-nextgen/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
