"""
Engine-agnostic standalone web service entry point.

Usage:
    python start.py [--port 20003] [--host 0.0.0.0] [--dev]
"""
from __future__ import annotations

import argparse
import logging


def main():
    parser = argparse.ArgumentParser(description="Engine adapter web service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=20003)
    parser.add_argument("--dev", action="store_true", help="Enable hot reload")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    import uvicorn
    uvicorn.run(
        "engine.community.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.dev,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
