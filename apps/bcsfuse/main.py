#!/usr/bin/env python3
"""
BCSFuse Open-Source Entry Point

This is the public entry point for BCSFuse, designed for open-source deployments.
It does NOT depend on internal-only infrastructure like sofapy_base or MIST.

Usage:
    # Direct uvicorn startup
    uvicorn main:app --host 0.0.0.0 --port 8765 --workers 2 --backlog 2048 --limit-concurrency 256

    # Or with environment variables
    BCSFUSE_PROVIDER_MODE=dev uvicorn main:app --host 0.0.0.0 --port 8765 --workers 1 --backlog 2048 --limit-concurrency 256

Modes:
    - runtime: Production mode with MySQL + Qdrant + Real HTTP providers
    - dev: Development mode with SQLite + Qdrant Local + Real HTTP providers
    - dev_smoke: Dev smoke test mode with SQLite + Qdrant Local + Fake/Noop providers (offline)
    - test: Test mode with InMemory + Fake/Noop providers

Environment Variables:
    BCSFUSE_PROVIDER_MODE: Provider mode (default: dev)
    BCSFUSE_PORT: Server port (default: 8765)

Note:
    Internal deployments use their host repository's composition entrypoint.
"""
import argparse
import logging
import faulthandler
import os
import sys

import uvicorn

# Enable faulthandler for crash diagnostics (R12-1)
# This prints Python traceback on fatal signals (SIGSEGV, SIGABRT, etc.)
# Can also be enabled via PYTHONFAULTHANDLER=1 environment variable
if os.getenv("PYTHONFAULTHANDLER", "0") == "1" or "--enable-faulthandler" in sys.argv:
    faulthandler.enable(file=sys.stderr, all_threads=True)
    print("[FAULTHANDLER] Enabled crash diagnostics", file=sys.stderr)

# Enable unbuffered stdout/stderr for real-time log output (R12-1)
# Can also be enabled via PYTHONUNBUFFERED=1 environment variable
if os.getenv("PYTHONUNBUFFERED", "0") == "1":
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    print("[UNBUFFERED] Enabled unbuffered stdout/stderr", file=sys.stderr)

logger = logging.getLogger(__name__)

# Get provider mode from environment
PROVIDER_MODE = os.getenv("BCSFUSE_PROVIDER_MODE", "dev")


def create_app():
    """
    Create FastAPI application for open-source deployment.

    Returns:
        FastAPI application instance
    """
    # CRITICAL: Configure logging before any business code runs.
    # This ensures all logger.info() calls output to stdout, which is
    # captured by nohup redirection in canonical startup script.
    # Without this, Python uses default WARNING level and suppresses INFO logs.
    from app.infra.logging.logging_config import configure_logging
    configure_logging()

    from app.bootstrap.opensource_app import create_opensource_app

    logger.info(f"[OpenSource Entrypoint] Creating app with mode: {PROVIDER_MODE}")
    app = create_opensource_app(mode=PROVIDER_MODE)

    logger.info("[OpenSource Entrypoint] App created successfully")
    return app


# Create application instance for uvicorn
app = create_app()


def _positive_int(value: str) -> int:
    """Parse a strictly positive server admission or backlog setting."""
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}") from exc
    if result <= 0:
        raise argparse.ArgumentTypeError(f"must be positive, got {value!r}")
    return result


def run_server(args: list[str] | None = None) -> None:
    """Start uvicorn with explicit process-level admission limits."""
    parser = argparse.ArgumentParser(
        description="BCSFuse Open-Source Server"
    )
    parser.add_argument(
        "--host", type=str, default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8765,
        help="Port to bind on (default: 8765)"
    )
    parser.add_argument(
        "--mode", type=str, choices=["runtime", "dev", "dev_smoke", "test"],
        default=None,
        help="Provider mode (default: from BCSFUSE_PROVIDER_MODE env var)"
    )
    parser.add_argument(
        "--reload", action="store_true",
        help="Enable auto-reload for development"
    )
    parser.add_argument(
        "--limit-concurrency",
        type=_positive_int,
        default=os.getenv("BCSFUSE_SERVER_LIMIT_CONCURRENCY", "256"),
        help=(
            "Maximum active HTTP tasks before uvicorn fails fast with 503 "
            "(default: BCSFUSE_SERVER_LIMIT_CONCURRENCY or 256)"
        )
    )
    parser.add_argument(
        "--backlog",
        type=_positive_int,
        default=os.getenv("BCSFUSE_SERVER_BACKLOG", "2048"),
        help="TCP accept backlog passed to uvicorn (default: 2048)"
    )
    parser.add_argument(
        "--workers",
        type=_positive_int,
        default=os.getenv("BCSFUSE_SERVER_WORKERS"),
        help=(
            "Number of server processes per Pod. Runtime plus Server-mode "
            "Qdrant defaults to 2; all other modes default to 1. Multiple "
            "workers require QDRANT_MODE=server "
            "(default: BCSFUSE_SERVER_WORKERS)"
        )
    )

    args = parser.parse_args(args)
    if args.mode:
        os.environ["BCSFUSE_PROVIDER_MODE"] = args.mode

    provider_mode = os.getenv("BCSFUSE_PROVIDER_MODE", "dev")
    qdrant_mode = os.getenv(
        "QDRANT_MODE", "server" if provider_mode == "runtime" else "local"
    ).strip().lower()
    if args.workers is None:
        args.workers = (
            2 if provider_mode == "runtime" and qdrant_mode == "server" else 1
        )
    if args.workers > 1 and qdrant_mode != "server":
        parser.error(
            "multiple workers require QDRANT_MODE=server; local Qdrant "
            "storage is process-exclusive"
        )

    print("=" * 70)
    print("BCSFuse Open-Source Server")
    print("=" * 70)
    print(f"Host: {args.host}")
    print(f"Port: {args.port}")
    print(f"Mode: {os.getenv('BCSFUSE_PROVIDER_MODE', 'dev')}")
    print(f"Reload: {args.reload}")
    print(f"Workers: {args.workers}")
    print(f"Admission limit: {args.limit_concurrency}")
    print(f"TCP backlog: {args.backlog}")
    print("=" * 70)
    print()
    print("Starting server...")
    print()

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers,
        backlog=args.backlog,
        limit_concurrency=None,
    )


if __name__ == "__main__":
    run_server()
