"""Root-anchored engine-socket route (transport-agnostic).

The backend's public connection endpoint publishes a finished socket address of
the shape ``wss://<gateway>/engine/<target>/api/<engine>/ws?x-proxypass-token=…``
and the gateway is what serves it: ``/engine/{rest}`` is rewritten onto the
engine proxy's own ``/proxypass/{rest}`` and everything past the prefix — the
routing target, the engine path, and the query carrying the credential — is
carried through verbatim.

This is **not** a :class:`~gateway.community.core.forwarding.DomainMap` domain.
A domain is the segment after ``base_path`` (``/openapi/v1``); this prefix is
anchored at the root of the gateway's host, which is why the backend refuses a
gateway base url with a path component — a path would push ``/engine`` off the
root and the rewrite would not be reachable. The anchor is therefore a constant
here too, not something configuration can move.

No web framework here (Rule 7): this is pure resolution logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from ._domains import Server, _parse_servers

#: Path prefix the gateway routes on, directly after the host. Fixed contract
#: with the backend, which spells the same constant when composing the URL.
ENGINE_PREFIX = "/engine"

#: The routing prefix the hop behind the gateway serves. The backend swaps this
#: for :data:`ENGINE_PREFIX` when publishing; this is the other half of it.
PROXYPASS_PREFIX = "/proxypass"

#: WebSocket scheme for each scheme the upstream may be configured with.
#: ``ws``/``wss`` are accepted so a deployment that spells the engine proxy out
#: as a socket origin is not rejected for being explicit. Mirrors the backend's
#: own map so one value serves both sides.
_WS_SCHEMES = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}


@dataclass(frozen=True)
class EngineRoute:
    """The configured engine-proxy upstream for the ``/engine`` prefix."""

    server: Server
    ws_base_url: str

    def upstream_url(self, path: str, query: str) -> str | None:
        """The upstream socket URL for *path*, or ``None`` if it is off-prefix.

        *path* must be the **raw** (still percent-encoded) request path and
        *query* the raw query string. Everything past the prefix is sliced out
        and re-emitted unchanged: decoding and re-encoding it would rewrite a
        target such as ``ARCA_x%40host%3A0`` into something the hop behind the
        gateway never published, and the contract for this prefix is that the
        tail travels exactly as the provider wrote it.

        ``/engine`` on its own has no tail to route and is off-prefix, as is any
        path that merely starts with the same letters (``/engineering/...``).
        """
        if not path.startswith(f"{ENGINE_PREFIX}/"):
            return None
        tail = path[len(ENGINE_PREFIX) :]
        url = f"{self.ws_base_url}{PROXYPASS_PREFIX}{tail}"
        return f"{url}?{query}" if query else url


def build_engine_route(
    raw: dict[str, Any], variables: dict[str, str]
) -> EngineRoute | None:
    """The engine route configured in ``user_config.upstreams``, if any.

    ``None`` is a real deployment state, not a missing value: a gateway that
    fronts no engine proxy serves no socket, and the route answers every
    handshake on the prefix by refusing it. That is the community build's normal
    state, and it mirrors the backend's neutral-empty ``gateway`` block — neither
    side publishes nor serves an address nothing answers.

    A block that *is* present is validated here rather than at the first
    handshake: an unknown server name or a base url whose scheme has no
    WebSocket equivalent fails startup, where an operator is looking.
    """
    configured = raw.get("engine")
    if not configured:
        return None
    if not isinstance(configured, dict):
        raise ValueError("upstreams.engine must be a mapping with a 'server' key")
    spec = cast(dict[str, Any], configured)

    server_name = str(spec.get("server", ""))
    servers = _parse_servers(raw.get("servers") or {}, variables)
    server = servers.get(server_name)
    if server is None:
        raise ValueError(f"upstreams.engine references unknown server {server_name!r}")
    return EngineRoute(server=server, ws_base_url=_ws_base_url(server))


def _ws_base_url(server: Server) -> str:
    """*server*'s base url as a WebSocket origin, without a trailing slash.

    Anchored on the scheme rather than substituted anywhere in the string: a
    host that happens to contain ``http://`` must not be rewritten too. Schemes
    are case-insensitive, and a value carrying none at all is refused rather
    than guessed at — defaulting to ``ws`` would silently serve a plaintext
    socket for a base url that meant ``https``.
    """
    base = server.base_url.strip().rstrip("/")
    scheme, separator, rest = base.partition("://")
    ws_scheme = _WS_SCHEMES.get(scheme.lower(), "") if separator else ""
    if not ws_scheme or not rest:
        raise ValueError(
            f"upstream server {server.name!r}: base_url {server.base_url!r} has no "
            f"scheme a websocket can be opened with (expected one of "
            f"{sorted(_WS_SCHEMES)})"
        )
    return f"{ws_scheme}://{rest}"
