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

import http.cookiejar
from typing import Any, Mapping

import httpx

from agentclaw.community.plugin_api.http_client import HttpClient


def _blocked_cookie_jar() -> http.cookiejar.CookieJar:
    """A jar that stores nothing and sends nothing.

    ``DefaultCookiePolicy(allowed_domains=[])`` refuses every domain in both
    directions, so the pooled client keeps the stateless per-call behavior it
    had when each request built its own client. Passing a ``CookieJar`` (rather
    than an ``httpx.Cookies``) is deliberate: ``httpx`` adopts a jar as-is but
    re-wraps a ``Cookies`` instance, which would drop the policy.
    """
    return http.cookiejar.CookieJar(
        policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[])
    )


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
        #
        # The cookie jar is blocked outright. A per-call client's jar died with
        # the call; a process-lifetime one would not. The first ``Set-Cookie``
        # the process ever sees — an LB stickiness cookie, a gateway session —
        # would then ride on every later request from every caller and every
        # tenant, pinning the process to one upstream (defeating the
        # ``device_affinity`` selection these calls exist to drive) and
        # potentially carrying one caller's identity into another's request.
        # Pooling connections must not also pool identity.
        self._client = httpx.Client(base_url=base_url, cookies=_blocked_cookie_jar())

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
