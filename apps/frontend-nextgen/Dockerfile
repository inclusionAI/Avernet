# syntax=docker/dockerfile:1.7
# Build context 必须是仓库根（本机路径形态）：
#   docker build -f apps/frontend-nextgen/Dockerfile .
# Monorepo 布局镜像内同构（review R6-1，同 docker/services/frontend-nextgen.dockerfile
# 的几何依据）：app 留在完整层级 apps/frontend-nextgen/，packages/ 同层级——
# config/tsconfig 的 '@avernet/a2a-chat-client' alias 上引路径原样成立。
FROM node:22-bookworm-slim AS build
# A2A 客户端构建期默认开关（spec §1）：验证/灰度镜像在 build 命令上
# --build-arg A2A_CLIENT_DEFAULT=true；缺省 false=主线 off 语义。
ARG A2A_CLIENT_DEFAULT=false
ENV A2A_CLIENT_DEFAULT=${A2A_CLIENT_DEFAULT}
WORKDIR /repo
COPY packages/a2a-chat-client/ ./packages/a2a-chat-client/
COPY apps/frontend-nextgen/package.json apps/frontend-nextgen/package-lock.json ./apps/frontend-nextgen/
RUN cd apps/frontend-nextgen && npm ci --registry=https://registry.npmjs.org
COPY apps/frontend-nextgen/ ./apps/frontend-nextgen/
RUN cd apps/frontend-nextgen && npm run build

FROM nginx:1.27-alpine
COPY apps/frontend-nextgen/deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY apps/frontend-nextgen/deploy/docker-entrypoint.sh /docker-entrypoint.d/20-validate-teamclaw-env.sh
COPY --from=build /repo/apps/frontend-nextgen/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
