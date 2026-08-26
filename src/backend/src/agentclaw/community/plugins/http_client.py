"""Real ``HttpClient`` — synchronous HTTP via a pooled ``httpx.Client``.

Neutral shared impl (like the unified repositories at ``plugins/``): one body for
every real deployment (corp, singlebox, community), bound explicitly per profile
under the qualified ``HttpClient`` keys. Lives at the ``plugins/`` root rather
than ``plugins/prod`` so the community column can bind it without importing the
corp overlay — it carries no corp coupling (only ``httpx`` + ``plugin_api``).

A ``base_url``-scoped client: every call issues its request against the relative
path and returns the raw :class:`httpx.Response`. Arguments left as ``None`` are
omitted so the wire shape matches a hand-written ``httpx`` call. Transport errors
and ``raise_for_status`` propagate unchanged (the wrapper swallows nothing).

Connection pooling
------------------
Each instance owns **one long-lived** ``httpx.Client``, built lazily on first use
and shared by every subsequent call, ``stream`` included. ``httpx.Client`` is
thread-safe, which is what makes this safe from the ``asyncio.to_thread`` worker
threads the callers of this seam reach it from. Because each qualified binding is
an injector ``@singleton``, one pool exists per upstream for the life of the
process: TCP + TLS handshakes are paid once instead of per call, and
``httpx.Limits`` puts a hard ceiling on how many sockets a burst of parallel
callers can open. Past the ceiling a request waits for a free connection and then
fails with ``httpx.PoolTimeout`` — a ``TimeoutException``, so it classifies as
``HttpClientTimeoutError`` like any other timeout — which is backpressure rather
than an unbounded fan-out.

Three consequences worth knowing:

* ``keepalive_expiry`` must stay below the upstream's own idle timeout. A
  connection the server has already closed but the pool still believes is live
  surfaces as ``httpx.RemoteProtocolError`` on the next request to pick it up.
  This failure mode does not exist without pooling, and nothing here retries it —
  the seam swallows nothing.
* A ``stream()`` holds its connection for the whole response body, so long-lived
  SSE streams occupy pool slots; size ``max_connections`` with that in mind.
* ``max_connections`` is a budget for the *whole* pool, not per origin. It
  matters for the ``general`` binding, whose ``base_url`` is ``""`` — callers
  pass absolute URLs, so its one pool spans every host they address.

HTTP/2
------
``http2=True`` lets many in-flight requests share one connection instead of one
each. Negotiation happens through TLS ALPN, so it takes effect on ``https://``
upstreams offering ``h2`` and silently stays on HTTP/1.1 elsewhere — httpx
performs no cleartext ``h2c`` upgrade, so a plain ``http://`` upstream is
unaffected either way. Requires the ``h2`` package (the ``httpx[http2]`` extra).
Defaults to off; the composition root turns it on from config.

Lifecycle
---------
``LifecycleBase.teardown`` closes the pool at process shutdown (phase 2, after
every participant's ``shutdown()`` has returned); discovery finds this instance
through its ``@singleton`` binding, so nothing has to be registered by hand.
``close()`` is idempotent and **terminal**: it drops the pool and latches the
instance closed, so a later call raises rather than quietly opening fresh
sockets. That matters because shutdown phase 2 drains *coroutines*, not the
``asyncio.to_thread`` workers this seam is usually called from —
``LLM._stream_read`` can still be reading an SSE body on such a worker when the
pool closes underneath it. Without the latch, the ``RuntimeError`` httpx raises
on the closed client reaches ``LLM``'s retry layer, which classifies it as
connection-level (no ``.response``) and retries, and the retry would build a
*new* pool after teardown that nothing will ever close.

A request racing teardown therefore fails. How loudly depends on the caller:
``RuntimeError`` is not an ``httpx.HTTPError``, so a handler catching transport
errors will not catch it — but ``LLM.chat()`` catches ``Exception`` broadly,
reads no ``.response`` off it, classifies it connection-level, and burns its
retry backoff (~8s of sleeps) before returning its ``[llm disabled]`` sentinel.
So on that path the failure is swallowed and shutdown is delayed rather than
surfaced.

That is the accepted cost of not reopening connections after teardown released
them. The alternative — letting the retry rebuild the pool — leaks a live pool
past shutdown, which is worse. Doing better would mean either tracking in-flight
requests here (bookkeeping this seam does not otherwise need) or teaching this
seam about a particular caller's retry classifier, which is backwards.
"""
from __future__ import annotations

import http.cookiejar
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

import httpx

from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.plugin_api.http_client import HttpClient

# Transport-policy defaults. Once the composition root is wired to config it
# passes explicit values for all four, so these govern only a direct
# construction (the singlebox endpoint fixture, unit tests); they are kept in
# step with the config dataclass defaults by hand.
DEFAULT_MAX_CONNECTIONS = 100
DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 20
DEFAULT_KEEPALIVE_EXPIRY = 5.0
DEFAULT_HTTP2 = False


class _DiscardingCookieJar(http.cookiejar.CookieJar):
    """A cookie jar that stores nothing.

    ``httpx.Client`` keeps a cookie jar for its own lifetime: it extracts
    ``Set-Cookie`` from every response and replays the result on every later
    request to the same host. That is harmless for a client that lives one call
    and actively wrong for a pooled one — this seam is a stateless RPC transport
    shared by every caller, and the ``general`` binding in particular carries
    per-user credentials to a single upstream (see the yuque router). A session
    cookie set by one caller's response must never ride along on the next
    caller's request.

    Overriding ``set_cookie`` is the whole fix: ``CookieJar.extract_cookies``
    funnels every accepted cookie through it, so discarding there leaves the jar
    permanently empty and nothing is ever sent back. Passing this as ``cookies=``
    is public API — ``httpx.Cookies.__init__`` adopts a ``CookieJar`` instance
    as-is rather than copying it into a fresh one.
    """

    def set_cookie(self, cookie: http.cookiejar.Cookie) -> None:
        return None


class HttpxClient(LifecycleBase, HttpClient):
    """Real ``httpx``-backed transport scoped to a single upstream ``base_url``.

    Owns one pooled, optionally HTTP/2-capable ``httpx.Client``, shared across
    every call and released at process shutdown.
    """

    def __init__(
        self,
        base_url: str,
        *,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        max_keepalive_connections: int = DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
        keepalive_expiry: float = DEFAULT_KEEPALIVE_EXPIRY,
        http2: bool = DEFAULT_HTTP2,
    ):
        self._base_url = base_url
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
            keepalive_expiry=keepalive_expiry,
        )
        self._http2 = http2
        # Guards lazy construction only; ``httpx.Client`` handles concurrent
        # requests itself, so the lock is never held across a request.
        self._lock = threading.Lock()
        self._client: httpx.Client | None = None
        # Latched by close(); a closed instance never builds another pool.
        self._closed = False

    # ── Pool ────────────────────────────────────────────────────────────

    def _pooled_client(self) -> httpx.Client:
        """The shared client, built on first use (double-checked under a lock).

        Lazy rather than eager because lifecycle discovery resolves every bound
        interface at boot; constructing here means a deployment never opens a
        pool for an upstream it does not call.
        """
        client = self._client
        if client is not None:
            return client
        with self._lock:
            if self._closed:
                raise RuntimeError(
                    "HttpxClient for "
                    f"{self._base_url or '<absolute-url caller>'} is closed; "
                    "the connection pool was released at shutdown and will not "
                    "be reopened."
                )
            if self._client is None:
                self._client = httpx.Client(
                    base_url=self._base_url,
                    limits=self._limits,
                    http2=self._http2,
                    cookies=_DiscardingCookieJar(),
                )
            return self._client

    def close(self) -> None:
        """Release the pool and latch the instance closed. Idempotent.

        Terminal by design: a later call raises rather than opening a fresh
        pool, so nothing reconnects to an upstream after teardown released it.
        """
        with self._lock:
            self._closed = True
            client, self._client = self._client, None
        # Closing outside the lock: ``close()`` walks the pool and can block, and
        # holding the construction lock across it would stall an unrelated caller
        # that only needs to build a fresh client.
        if client is not None:
            client.close()

    async def teardown(self) -> None:
        """Lifecycle phase 2 of shutdown — release the connection pool."""
        self.close()

    # ── Requests ────────────────────────────────────────────────────────

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

    def patch(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        return self._request(
            "PATCH", path, params=params, json=json, headers=headers, timeout=timeout
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
        # ``timeout`` is per-request now that the client outlives the call. A bare
        # float expands to the same connect/read/write/pool budget httpx applied
        # when it was a constructor argument, so the per-call budget is unchanged.
        return self._pooled_client().request(method, path, timeout=timeout, **kwargs)

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
        ``with`` block (``resp.iter_lines()`` / ``raise_for_status()``).

        Shares the pooled client with ``post`` and friends: the connection
        returns to the pool when the block exits, and the client itself stays
        open for later calls. Transport errors and ``raise_for_status`` propagate
        unchanged.
        """
        kwargs: dict[str, Any] = {}
        if params is not None:
            kwargs["params"] = params
        if json is not None:
            kwargs["json"] = json
        if headers is not None:
            kwargs["headers"] = headers
        with self._pooled_client().stream(
            method, path, timeout=timeout, **kwargs
        ) as resp:
            yield resp
