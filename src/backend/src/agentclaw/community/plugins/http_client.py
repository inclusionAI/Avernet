"""Real ``HttpClient`` — synchronous HTTP via ``httpx.Client``.

Neutral shared impl (like the unified repositories at ``plugins/``): one body for
every real deployment (corp, singlebox, community), bound explicitly per profile
under the qualified ``HttpClient`` keys. Lives at the ``plugins/`` root rather
than ``plugins/prod`` so the community column can bind it without importing the
corp overlay — it carries no corp coupling (only ``httpx`` + ``plugin_api``).

A ``base_url``-scoped client: one ``httpx.Client(base_url=...)`` is built at
construction and reused for every call, so requests to the same host keep their
connection instead of paying a fresh TCP + TLS handshake each time. Requests are
issued against the relative path, returning the raw :class:`httpx.Response`.
Arguments left as ``None`` are omitted so the wire shape matches a hand-written
``httpx`` call. Transport errors and ``raise_for_status`` propagate unchanged
(the wrapper swallows nothing).
"""
from __future__ import annotations

from typing import Any, Mapping

import httpx

from agentclaw.community.plugin_api.http_client import HttpClient


class HttpxClient(HttpClient):
    """Real ``httpx``-backed transport scoped to a single upstream ``base_url``."""

    def __init__(self, base_url: str):
        self._base_url = base_url
        # Eager, not lazy: ``httpx.Client()`` opens no socket, so construction
        # does no I/O and needs no lock. ``httpx.Client`` is itself thread-safe
        # for requests, which is what these process-lifetime DI singletons need
        # when reached from thread-pool workers. Timeout is deliberately not set
        # here — it is per-call, and passing it at construction would freeze
        # every caller's deadline to whoever built the client.
        self._client = httpx.Client(base_url=base_url)

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        return self._request("GET", path, params=params, headers=headers, timeout=timeout)

    def post(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        files: Any | None = None,
        data: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        return self._request(
            "POST",
            path,
            params=params,
            json=json,
            files=files,
            data=data,
            headers=headers,
            timeout=timeout,
        )

    def put(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        return self._request(
            "PUT", path, params=params, json=json, headers=headers, timeout=timeout
        )

    def delete(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        return self._request(
            "DELETE", path, params=params, headers=headers, timeout=timeout
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        files: Any | None = None,
        data: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        kwargs: dict[str, Any] = {}
        if params is not None:
            kwargs["params"] = params
        if json is not None:
            kwargs["json"] = json
        if files is not None:
            kwargs["files"] = files
        if data is not None:
            kwargs["data"] = data
        if headers is not None:
            kwargs["headers"] = headers
        return self._client.request(method, path, timeout=timeout, **kwargs)
