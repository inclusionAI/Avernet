# Build from repository root: docker build -f apps/evolution/Dockerfile .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy
WORKDIR /workspace/apps/evolution
COPY packages/genome-contracts /workspace/packages/genome-contracts
COPY apps/evolution/pyproject.toml apps/evolution/uv.lock ./
COPY apps/evolution/src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid 10001 evolution && useradd --uid 10001 --gid evolution --create-home evolution \
    && mkdir -p /var/lib/evolution && chown evolution:evolution /var/lib/evolution
COPY --from=build /opt/venv /opt/venv
WORKDIR /var/lib/evolution
USER evolution
EXPOSE 8091
ENTRYPOINT ["evolution", "serve"]
