"""HTTP-backed schema catalog — fetches each domain's published OpenAPI from a remote URL.

Deployed / multi-instance flavor: a domain's published description lives at an
HTTP(S) endpoint (object store, CI artifact server, schema registry). A
background refresh fetches the URLs periodically and swaps the in-memory copy;
on a network error, bad status, or parse failure the previous **known-good**
copy is kept, so an unreachable server never blanks the served doc.

Conditional requests (``If-None-Match`` based on ETag, ``If-Modified-Since``
based on ``Last-Modified``) avoid re-downloading unchanged schemas.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import httpx
import yaml

from gateway.community.spi.schema_catalog import SchemaCatalog

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class HttpSchemaCatalog(SchemaCatalog):
    """HTTP-backed :class:`SchemaCatalog` with last-known-good semantics.

    Construct with ``sources`` mapping domain → URL. Call
    :meth:`refresh_all` once at startup to prime the cache, then run
    :meth:`refresh_loop` as a background task.
    """

    def __init__(self, sources: Mapping[str, str] | None = None) -> None:
        self._sources: dict[str, str] = dict(sources or {})
        self._cache: dict[str, dict[str, Any]] = {}
        self._etags: dict[str, str] = {}
        self._last_modified: dict[str, str] = {}
        self._client: httpx.AsyncClient | None = None

    def set_sources(self, sources: Mapping[str, str]) -> None:
        """Replace the source map (used by DI wiring to inject config-derived URLs)."""
        self._sources = dict(sources)

    def current(self, domain: str) -> dict[str, Any]:
        return self._cache.get(domain, {})

    def refresh(self, domain: str) -> bool:
        """Fetch *domain*'s URL synchronously. Returns True on adoption; keeps old on failure."""
        url = self._sources.get(domain)
        if url is None:
            return False
        return _run_sync(self._refresh_async(domain, url))

    def refresh_all(self) -> None:
        for domain in self._sources:
            self.refresh(domain)

    async def refresh_loop(self, interval_seconds: float, stop: asyncio.Event) -> None:
        """Refresh every ``interval_seconds`` until *stop* is set."""
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                self._client = client
                while not stop.is_set():
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
                    except TimeoutError:
                        pass
                    if stop.is_set():
                        return
                    await self._refresh_all_async()
        finally:
            self._client = None

    async def _refresh_all_async(self) -> None:
        for domain, url in list(self._sources.items()):
            try:
                await self._refresh_async(domain, url)
            except Exception as exc:
                logger.warning(
                    "schema refresh failed for %s (%s): %s", domain, url, exc
                )

    async def _refresh_async(self, domain: str, url: str) -> bool:
        client = self._get_client()
        headers = _build_conditional_headers(
            self._etags.get(domain), self._last_modified.get(domain)
        )
        try:
            response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("schema refresh failed for %s (%s): %s", domain, url, exc)
            return False

        if response.status_code == 304:
            return True

        if response.status_code < 200 or response.status_code >= 300:
            logger.warning(
                "schema refresh for %s (%s): HTTP %s",
                domain,
                url,
                response.status_code,
            )
            return False

        try:
            parsed = _parse_body(response)
        except Exception as exc:
            logger.warning(
                "schema refresh for %s (%s): parse failed: %s",
                domain,
                url,
                exc,
            )
            return False

        if not isinstance(parsed, dict):
            logger.warning(
                "schema for %s is not a mapping; keeping last known-good", domain
            )
            return False

        _store_conditional_headers(response, domain, self._etags, self._last_modified)
        self._cache[domain] = parsed
        return True

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)


def _run_sync(coro: Any) -> bool:
    """Run an async coroutine synchronously, working in both test and event-loop contexts."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    future = concurrent.futures.Future[bool]()

    async def _runner() -> None:
        try:
            result = await coro
        except Exception as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)

    loop.create_task(_runner())
    return future.result(timeout=_DEFAULT_TIMEOUT)


def _parse_body(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        return response.json()
    return yaml.safe_load(response.text)


def _build_conditional_headers(
    etag: str | None, last_modified: str | None
) -> dict[str, str]:
    headers: dict[str, str] = {
        "Accept": "application/json, application/yaml",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return headers


def _store_conditional_headers(
    response: httpx.Response,
    domain: str,
    etags: dict[str, str],
    last_modified: dict[str, str],
) -> None:
    tag = response.headers.get("etag")
    if tag:
        etags[domain] = tag
    modified = response.headers.get("last-modified")
    if modified:
        last_modified[domain] = modified
