"""Real ``HttpClient`` — synchronous HTTP via ``httpx.Client``.

Neutral shared impl (like the unified repositories at ``plugins/``): one body for
every real deployment (corp, singlebox, community), bound explicitly per profile
under the qualified ``HttpClient`` keys. Lives at the ``plugins/`` root rather
than ``plugins/prod`` so the community column can bind it without importing the
corp overlay — it carries no corp coupling (only ``httpx`` + ``plugin_api``).

A ``base_url``-scoped client: every call opens a short-lived
``httpx.Client(base_url=...)`` and issues the request against the relative path,
returning the raw :class:`httpx.Response`. Arguments left as ``None`` are omitted
so the wire shape matches a hand-written ``httpx`` call. Transport errors and
``raise_for_status`` propagate unchanged (the wrapper swallows nothing).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping

import httpx

from agentclaw.community.plugin_api.http_client import HttpClient


class HttpxClient(HttpClient):
    """Real ``httpx``-backed transport scoped to a single upstream ``base_url``."""

    def __init__(self, base_url: str, *, transport: Any | None = None):
        self._base_url = base_url
        self._transport = transport

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
        client_kwargs = {"base_url": self._base_url, "timeout": timeout}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        with httpx.Client(**client_kwargs) as client:
            return client.request(method, path, **kwargs)

    @contextmanager
    def stream(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> Iterator[httpx.Response]:
        """Stream a request and yield a streaming ``httpx.Response`` for the
        ``with`` block (``resp.iter_lines()`` / ``raise_for_status()``). Mirrors
        ``post``'s short-lived-client pattern; transport errors and
        ``raise_for_status`` propagate unchanged. The test-injected
        ``transport`` (``httpx.MockTransport``) makes the streaming seam
        conformance-testable without a real network.
        """
        kwargs: dict[str, Any] = {}
        if params is not None:
            kwargs["params"] = params
        if json is not None:
            kwargs["json"] = json
        if headers is not None:
            kwargs["headers"] = headers
        client_kwargs = {"base_url": self._base_url, "timeout": timeout}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        with httpx.Client(**client_kwargs) as client:
            with client.stream(method, path, **kwargs) as resp:
                yield resp
