"""SecretResolver SPI — abstract retrieval of a named secret from a credential backend.

Mirrors the backend ``agentclaw.community.plugin_api.secret_resolver.SecretResolver``
contract so the gateway can resolve signing keys (and other credentials) through
the same abstraction. Implementations are selected by deploy profile: community
reads from the process environment; enterprise may resolve from a corp secret
store / KMS. The gateway Protocol is a plain ``Protocol`` (gateway SPI
convention) rather than the backend ``Plugin`` base.

``get_secret`` returns an object exposing ``.secret_user`` / ``.secret_value``
(community flavor) or ``None`` when the backend has no such secret; consumers
must accept that shape. The SPI intentionally exposes only ``get_secret``; the
gateway does not need the wider BaaS ``SecretStorePlugin`` surface
(``resolve_secret``, ``get_kv_secret``, ``generate_proxy_token``,
``resolve_common_sm4_key``, ``close``) gated by this Protocol.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SecretResolver(Protocol):
    """Resolve a named secret to its credential object.

    Returns an object exposing ``.secret_user`` / ``.secret_value`` (duck-typed
    shape), a plain ``str`` value, or ``None`` when the backend has no such
    secret. Backend/transport errors are **not** swallowed — they propagate so
    callers that need a fallback must wrap the call in ``try/except``.
    """

    def get_secret(self, secret_name: str) -> Any | None:
        """Return the secret object for ``secret_name``, or ``None`` if absent."""
        ...
