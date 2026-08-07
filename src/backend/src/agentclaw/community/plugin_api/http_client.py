"""``HttpClient`` — a base-URL-scoped, mockable synchronous-HTTP transport seam.

A thin wrapper over synchronous HTTP. Each client is constructed with a
``base_url`` (the host of one upstream service), and its methods take a
**relative path**; the full request URL is ``base_url + path``. Services that
call an external HTTP API (BaaS, BCN, …) depend on this Protocol instead of
constructing an ``httpx.Client`` inline, so tests inject a configurable mock —
assert the request happened, stub the response — and never touch the network.

The return value is an :class:`httpx.Response`, the transport's natural
currency. Callers use ``response.raise_for_status()`` / ``response.json()`` /
``response.status_code`` exactly as against a raw ``httpx.Client``, and error
semantics are unchanged (``httpx.HTTPStatusError`` from ``raise_for_status``,
``httpx.TimeoutException`` on a transport timeout).

Qualified bindings (Guice-style ``@Named``)
-------------------------------------------
The bound type is always ``HttpClient`` — there is no per-upstream subclass.
Several pre-configured instances are distinguished purely by an injector
*qualifier* (the string in ``Annotated``), so a single Protocol is bound to one
client per upstream host::

    binder/provider →  Annotated[HttpClient, "baas"]   # base_url = BaaS API
                       Annotated[HttpClient, "bcn"]     # base_url = BCN API

A consumer injects the qualified key (``baas_http: Annotated[HttpClient,
"baas"]``); a test fetches the same singleton (``world.get(Annotated[HttpClient,
"baas"])``), stubs it with ``set_response`` / ``set_override``, and asserts with
``.calls_to("post")``. Qualifier constants live here to avoid typos.

Impls (Rule 20/21):

* ``prod`` → ``HttpxClient(base_url)`` — one real ``httpx.Client``, built at
  construction and reused across calls so connections stay open.
* ``local`` → ``LocalHttpClient(base_url)`` (``MockSeam``, flavor ``MOCK``) — an
  unstubbed call **raises** so a test can never silently hit the network.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

import httpx

from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class HttpClient(Plugin, Protocol):
    """Base-URL-scoped synchronous HTTP transport.

    Each method issues one request to ``base_url + path`` and returns the raw
    :class:`httpx.Response`. ``timeout`` is per-call (seconds). Arguments left as
    ``None`` are omitted from the underlying request so the wire shape matches a
    direct ``httpx.Client`` call.
    """

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        """Issue a GET to ``base_url + path`` and return the response."""
        ...

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
        """Issue a POST to ``base_url + path`` and return the response.

        ``files``/``data`` carry a multipart upload (mutually exclusive with
        ``json``); like every other arg, ``None`` omits them so the wire shape
        matches a direct ``httpx.Client`` call.
        """
        ...

    def put(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        """Issue a PUT to ``base_url + path`` and return the response."""
        ...

    def delete(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        """Issue a DELETE to ``base_url + path`` and return the response."""
        ...


# ── Injector qualifiers (Guice @Named-style) ─────────────────────────────────
# The SAME ``HttpClient`` Protocol, bound once per upstream. Bind/inject as
# ``Annotated[HttpClient, QUALIFIER_BAAS]`` — never the bare ``HttpClient``.
QUALIFIER_BAAS = "baas"
QUALIFIER_BCN = "bcn"
QUALIFIER_GENERAL = "general"
QUALIFIER_MASA_AGENT_EVAL = "masa_agent_eval"
"""General-purpose HttpClient with ``base_url=""`` — callers pass full absolute
URLs so no base-URL prefix is prepended. Use when the target host varies
per-call (e.g. BaaS ``invoke_http`` / ``get_http_info`` where the container
URL is resolved at runtime from BaaS and differs from the BaaS host itself)."""
