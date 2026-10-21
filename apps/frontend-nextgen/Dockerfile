# syntax=docker/dockerfile:1.7
# Build context 必须是仓库根（本机路径形态）：
#   docker build -f apps/frontend-nextgen/Dockerfile .
# @avernet/a2a-chat-client 以 alias 直连 packages/ 源码——context 需含包目录
#（docker/services/frontend-nextgen.dockerfile 是 CI 实际引用的正式构建位）。
FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY packages/a2a-chat-client/ ./packages/a2a-chat-client/
COPY apps/frontend-nextgen/package.json apps/frontend-nextgen/package-lock.json ./
RUN npm ci --registry=https://registry.npmjs.org
COPY apps/frontend-nextgen/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY apps/frontend-nextgen/deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY apps/frontend-nextgen/deploy/docker-entrypoint.sh /docker-entrypoint.d/20-validate-teamclaw-env.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1