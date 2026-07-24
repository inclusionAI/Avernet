"""SchemaCatalog Protocol — the current published OpenAPI description per domain.

The catalog is a **doc-only** input: it feeds OpenAPI generation and nothing
else. Routing, auth, and forwarding never call it, so a stale or unreachable
source degrades only the served doc, never live traffic.
"""

from __future__ import annotations

from typing import Any, Protocol


class SchemaCatalog(Protocol):
    """Serves the latest known-good published description for a domain.

    Implementations keep an in-memory copy that a background refresh updates;
    ``current`` is a fast, non-blocking read.

    Implementations:
    - BareSchemaCatalog: reads a committed local file (single-box default).
    - a sofa flavor (enterprise) reads an object store (OSS) and auto-adopts
      the latest published version.
    """

    def current(self, domain: str) -> dict[str, Any]:
        """The latest known-good description for *domain* (``{}`` if none yet)."""
        ...
