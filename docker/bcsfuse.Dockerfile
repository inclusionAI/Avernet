# BCSFuse Runtime Image for ACK / Aliyun Deployment
#
# Build context: repository root (so we can SELECTIVELY copy only src/bcsfuse).
# This keeps internal modules and other secrets out of the bcsfuse image.
#
# Usage from repo root:
#   docker build -f docker/bcsfuse.Dockerfile -t bcsfuse:local .
#
# Run smoke check:
#   docker run --rm -p 8765:8765 -e BCSFUSE_PROVIDER_MODE=dev_smoke bcsfuse:local

FROM python:3.12-slim-bookworm

# Public-safe global defaults. Concrete configuration must be supplied at
# runtime via Kubernetes Secret / ConfigMap (never baked into the image).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    BCSFUSE_PROVIDER_MODE=runtime \
    BCSFUSE_PORT=8765 \
    BCSFUSE_SERVER_HOST=0.0.0.0 \
    SERVICE_HOST=0.0.0.0 \
    SERVICE_PORT=8765

# Runtime dependencies we need before pip install can compile extensions.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libssl-dev \
        libmysqlclient-dev \
        pkg-config \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user.
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy package metadata first for layer caching.
COPY src/bcsfuse/pyproject.toml src/bcsfuse/README.md ./

# Install package in editable mode with dev extras (tests are kept in image
# for optional container self-verification; they are not run at startup).
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e ".[dev]"

# Copy only the public bcsfuse source tree. We intentionally avoid copying
# src/backend, src/bcs, src/frontend, or any sibling internal modules.
COPY src/bcsfuse/src ./src
COPY src/bcsfuse/schemas ./schemas
COPY src/bcsfuse/configs ./configs
COPY src/bcsfuse/main.py ./

# Copy legal / deployment notes only; no live credentials.
COPY src/bcsfuse/LEGAL.md src/bcsfuse/FUSE_API_LOGIC.md ./

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')" || exit 1

CMD ["python", "main.py"]
