########## Builder ##########
FROM python:3.12-slim-bookworm AS builder

# Pin uv for reproducible builds, e.g. --build-arg UV_VERSION=0.8.14
ARG UV_VERSION=

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources \
    && pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple "uv${UV_VERSION:+==${UV_VERSION}}"

# Install dependencies first (without the project) for better layer caching.
# The default uv index is the Aliyun PyPI mirror (see src/gateway/pyproject.toml).
COPY src/gateway/pyproject.toml src/gateway/uv.lock src/gateway/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Then install the project itself — main.py discovers the runner through the
# package's installed entry points, so this step is required, not optional.
COPY src/gateway/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

########## Runtime ##########
FROM python:3.12-slim-bookworm AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/home/admin \
    PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        procps \
        net-tools \
        iproute2 \
        iputils-ping \
        vim \
        less \
        jq \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 admin \
    && useradd --uid 10001 --gid admin --create-home --shell /bin/bash admin

COPY --from=builder /app/.venv /app/.venv
COPY src/gateway/src /app/src
COPY src/gateway/configs /app/configs

# tmp/: scratch space used by scripts/app.sh conventions.
# ~/logs/: default log location ($HOME/logs).
# SQLite default lives in /tmp and is world-writable.
RUN mkdir -p /app/tmp /home/admin/logs \
    && chown -R admin:admin /app /home/admin

# The service runs as admin (non-root). Root stays available for debugging —
# it is merely password-locked, and container exec needs no password:
#   kubectl exec -it <pod> -u 0 -- bash    (then apt-get install ...)
# Root password stays unset on purpose: no secret ships in image layers.
USER admin

EXPOSE 8080

# GATEWAY_PORT only steers the healthcheck; the real listen port comes from
# module_config.web.port in the mounted config. Keep them in sync.
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
    CMD curl -fsS "http://127.0.0.1:${GATEWAY_PORT:-8888}/health" >/dev/null || exit 1

# Entry point selects the runner via installed entry points (bare = community).
ENTRYPOINT ["python", "/app/src/gateway/community/main.py"]
CMD ["--config", "/app/configs", "--mode", "bare"]
