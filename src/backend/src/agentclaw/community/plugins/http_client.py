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

Because the client now outlives the call, it is a lifecycle participant: it
implements :class:`Lifecycle` via :class:`LifecycleBase` and closes the pool in
``teardown()``, so the connections it holds are released at process shutdown
rather than leaked.
"""
from __future__ import annotations

import http.cookiejar
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

import httpx

from agentclaw.community.kernel.lifecycle import LifecycleBase
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


class HttpxClient(HttpClient, LifecycleBase):
    """Real ``httpx``-backed transport scoped to a single upstream ``base_url``.

    Inherits :class:`LifecycleBase` so ``discover_lifecycle_participants`` finds
    it — ``Lifecycle`` is ``@runtime_checkable``, so ``isinstance`` succeeds only
    when all four hooks exist, and the base supplies the three this class does
    not override as no-ops.
    """

    def __init__(self, base_url: str, *, transport: Any | None = None):
        self._base_url = base_url
        self._transport = transport
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
        client_kwargs: dict[str, Any] = {
            "base_url": base_url,
            "cookies": _blocked_cookie_jar(),
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.Client(**client_kwargs)

    async def teardown(self) -> None:
        """Release the connection pool at process shutdown.

        Infrastructure teardown rather than ``shutdown()``: by this phase every
        participant's ``shutdown()`` has returned, so no service-tier code
        should still be issuing requests through this client.

        ``httpx.Client.close()`` is synchronous and only closes already-idle
        sockets, so it does not block the loop meaningfully. Teardown failures
        are log-and-continue per the lifecycle contract, so a raise here cannot
        strand other participants.
        """
        self._client.close()

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
        ``with`` block (``resp.iter_lines()`` / ``raise_for_status()``). Shares
        the pooled client with ``_request`` rather than opening a short-lived
        one, so a stream reuses connections like every other call — and so it
        cannot outlive the pool's lifecycle teardown. Only the *stream* is
        closed on exit; the client stays open. Transport errors and
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
        with self._client.stream(method, path, timeout=timeout, **kwargs) as resp:
            yield resp
