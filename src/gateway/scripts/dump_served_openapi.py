"""Dump the served OpenAPI document — the one third-party clients actually read.

This is a different document from the ones in ``configs/schemas/``. Those are
each upstream's description of its **own** surface and are the catalog *input*:
the backend's ``bots.openapi.json`` describes what the backend serves, and it
carries no auth at all, because the backend never sees a caller's credential —
it receives the signed ``X-Avernet-Principal`` the gateway mints.

What a client reads is the document the gateway *composes* and serves at
``/openapi.json``: public-namespace paths, gateway-facing after path rewrites,
carrying the auth the gateway itself enforces —

- ``components.securitySchemes`` + per-operation ``security``: the credential a
  caller must present, in standard OpenAPI, so Swagger UI renders an Authorize
  control and code generators emit an SDK with an auth parameter;
- ``x-avernet-security``: the internal record of which identities the gateway
  resolves into the signed principal.

Until now that document existed only at runtime, so it could not be reviewed in
a diff or checked for regressions. This writes it to a file so it can be.

It is built through the real composition root (``bootstrap_app``), the same call
the web app makes, so the artifact cannot describe anything other than what a
gateway booted on this configuration would serve. It therefore reflects *this*
deployment's configured credentials: a community deployment documents whatever
header its user strategy reads, and an enterprise one documents its own, because
each strategy declares its own credential.

Usage:

    uv run python scripts/dump_served_openapi.py [dest]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEFAULT_DEST = Path("configs/schemas/served/gateway.openapi.json")


def build_served_document() -> dict[str, Any]:
    """The served document, composed exactly as the running gateway composes it."""
    from gateway.community import __version__
    from gateway.community.bootstrap import bootstrap_app
    from gateway.community.config import ConfigLoader

    config = ConfigLoader.load()
    bootstrap = bootstrap_app()
    try:
        return dict(
            bootstrap.served_openapi(
                title=config.app_name,
                version=__version__,
                description=(
                    "Avernet Gateway — A configuration-driven forwarding plane "
                    "(UNDER ACTIVE DEVELOPMENT)."
                ),
            )
        )
    finally:
        bootstrap.shutdown()


def dump_served_openapi(target: str | Path = _DEFAULT_DEST) -> dict[str, Any]:
    """Write the served document to *target* (deterministic JSON)."""
    document = build_served_document()
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return document


if __name__ == "__main__":  # pragma: no cover - CLI entry for CI
    import sys

    dest = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_DEST
    dump_served_openapi(dest)
    print(f"wrote served OpenAPI to {dest}")
