# syntax=docker/dockerfile:1
# -----------------------------------------------------------------------------
# secbaas (BaaS) image — single-box runtime of the AI bot platform.
#
# Build (from the repo root, same convention as Dockerfile.ocb):
#   docker build -f docker/baas.dockerfile -t secbaas:local .
#   docker build -f docker/baas.dockerfile --build-arg USE_CN_MIRROR=1 -t secbaas:local .
#
# Run (bare mode, default config, port 8888):
#   docker run --rm -p 8888:8888 secbaas:local
#
# Run with the singlebox config (port 8890):
#   docker run --rm -p 8890:8890 -e BAAS_PORT=8890 \
#     secbaas:local --config /app/singlebox-configs --mode bare
#
# Mount a custom config dir or overlay:
#   docker run --rm -p 8888:8888 \
#     -v "$PWD/my-configs:/app/configs:ro" secbaas:local
#   docker run --rm -p 8888:8888 \
#     -v "$PWD/my-overlay.yaml:/app/overlays/my-overlay.yaml:ro" \
#     -e SOFAPY_CONFIG_OVERLAY=/app/overlays/my-overlay.yaml \
#     secbaas:local
#
# Useful env vars (read by the app, see src/baas README):
#   SERVER_ENV             — env overlay name (dev, prepub, prod)
#   SOFAPY_CONFIG_OVERLAY  — extra YAML overlay merged on top of the base config
#   BAAS_PORT              — healthcheck port only; must match module_config.web.port
# -----------------------------------------------------------------------------

########## Builder ##########
FROM python:3.12-slim-bookworm AS builder

# Single mirror switch. Empty = official upstream (international default).
# Set USE_CN_MIRROR=1 for China-friendly mirrors; see docs/docker.md.
ARG USE_CN_MIRROR=
# Pin uv for reproducible builds, e.g. --build-arg UV_VERSION=0.8.14
ARG UV_VERSION=

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN if [ "${USE_CN_MIRROR}" = "1" ]; then \
        sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && if [ "${USE_CN_MIRROR}" = "1" ]; then \
        PIP_INDEX="https://mirrors.aliyun.com/pypi/simple"; \
    else \
        PIP_INDEX="https://pypi.org/simple"; \
    fi \
    && pip install --no-cache-dir -i "${PIP_INDEX}" "uv${UV_VERSION:+==${UV_VERSION}}"

# Install dependencies first (without the project) for better layer caching.
# The default uv index is the Aliyun PyPI mirror (see src/baas/pyproject.toml).
COPY src/baas/pyproject.toml src/baas/uv.lock src/baas/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Then install the project itself — main.py discovers the runner through the
# package's installed entry points, so this step is required, not optional.
COPY src/baas/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

########## Runtime ##########
FROM python:3.12-slim-bookworm AS runtime

ARG USE_CN_MIRROR=

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/home/admin \
    PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN if [ "${USE_CN_MIRROR}" = "1" ]; then \
        sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 admin \
    && useradd --uid 10001 --gid admin --create-home --shell /bin/bash admin

COPY --from=builder /app/.venv /app/.venv
COPY src/baas/src /app/src
COPY src/baas/configs /app/configs
COPY src/baas/singlebox-configs /app/singlebox-configs

# tmp/: scratch space used by scripts/app.sh conventions.
# ~/logs/: default SOFAPy-style log location ($HOME/logs/secbaas).
# SQLite default (sqlite:////tmp/secbaas.db) lives in /tmp and is world-writable.
RUN mkdir -p /app/tmp /home/admin/logs \
    && chown -R admin:admin /app /home/admin

USER admin

EXPOSE 8888

# BAAS_PORT only steers the healthcheck; the real listen port comes from
# module_config.web.port in the mounted config. Keep them in sync.
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
    CMD curl -fsS "http://127.0.0.1:${BAAS_PORT:-8888}/health" >/dev/null || exit 1

# Entry point selects the runner via installed entry points (bare = community).
# Override CMD to switch config dir or mode, e.g.:
#   --config /app/singlebox-configs --mode bare
ENTRYPOINT ["python", "/app/src/secbaas/community/main.py"]
CMD ["--config", "/app/configs", "--mode", "bare"]
