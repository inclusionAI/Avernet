"""Bare schema catalog — reads each domain's committed OpenAPI file.

Single-box / open-source flavor: a domain's published description is a local
JSON (or YAML) file. A background refresh re-reads the files periodically and
swaps the in-memory copy; on a read or parse failure the previous **known-good**
copy is kept, so a bad file never blanks the served doc.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from gateway.community.spi.schema_catalog import SchemaCatalog

logger = logging.getLogger(__name__)


class BareSchemaCatalog(SchemaCatalog):
    """File-backed :class:`SchemaCatalog` with last-known-good semantics.

    Construct with ``sources`` mapping domain → file path. Call
    :meth:`refresh_all` once at startup to prime the cache, then run
    :meth:`refresh_loop` as a background task.
    """

    def __init__(self, sources: Mapping[str, str | Path] | None = None) -> None:
        self._sources: dict[str, Path] = {
            d: Path(p) for d, p in (sources or {}).items()
        }
        self._cache: dict[str, dict[str, Any]] = {}

    def current(self, domain: str) -> dict[str, Any]:
        return self._cache.get(domain, {})

    def refresh(self, domain: str) -> bool:
        """Re-read *domain*'s file. Returns True on adoption; keeps old on failure."""
        path = self._sources.get(domain)
        if path is None:
            return False
        try:
            parsed = _parse(path)
        except Exception as exc:  # doc-only refresher must never crash the loop
            logger.warning("schema refresh failed for %s (%s): %s", domain, path, exc)
            return False
        if not isinstance(parsed, dict):
            logger.warning(
                "schema for %s is not a mapping; keeping last known-good", domain
            )
            return False
        self._cache[domain] = parsed
        return True

    def refresh_all(self) -> None:
        for domain in self._sources:
            self.refresh(domain)

    async def refresh_loop(self, interval_seconds: float, stop: asyncio.Event) -> None:
        """Refresh every ``interval_seconds`` until *stop* is set."""
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                pass
            if stop.is_set():
                return
            self.refresh_all()


def _parse(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    return yaml.safe_load(text)
