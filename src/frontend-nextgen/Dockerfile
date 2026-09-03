# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --registry=https://registry.npmjs.org
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY deploy/docker-entrypoint.sh /docker-entrypoint.d/20-validate-teamclaw-env.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
