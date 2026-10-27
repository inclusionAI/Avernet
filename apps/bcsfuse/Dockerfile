# BCSFuse Open-Source Docker Image
# OPENCORE-P1-G8 Docker Deployment Gate
#
# This Dockerfile creates a public-safe runtime image for BCSFuse open-core.
# It does NOT include internal dependencies (bcsfuse_internal, ant-sofapy-base, etc.)
#
# Build:
#   docker build -f apps/bcsfuse/Dockerfile -t bcsfuse-opencore:p1-g8 .
# Run the build from the repository root so the shared package uses the same revision.
#
# Run:
#   docker compose -f apps/bcsfuse/docker-compose.yml up -d
#
# Test:
#   curl http://localhost:8765/health
#   curl http://localhost:8765/ready

FROM python:3.12-slim

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BCSFUSE_PROVIDER_MODE=dev_smoke \
    BCSFUSE_PORT=8765 \
    QDRANT_MODE=server

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

# Set working directory
WORKDIR /app

# Copy dependency files first for better layer caching
COPY apps/bcsfuse/packages/search-core /opt/search-core
COPY apps/bcsfuse/pyproject.toml apps/bcsfuse/README.md ./

# Copy source before installing the src-layout application.
COPY apps/bcsfuse/src ./src
COPY apps/bcsfuse/tests ./tests
COPY apps/bcsfuse/schemas ./schemas
COPY apps/bcsfuse/configs ./configs
COPY apps/bcsfuse/main.py ./

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir /opt/search-core -e ".[dev]"

# Copy documentation and legal
COPY apps/bcsfuse/LEGAL.md apps/bcsfuse/FUSE_API_LOGIC.md ./

# Set ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8765

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/ready')" || exit 1

# Default command
CMD ["python", "main.py"]
