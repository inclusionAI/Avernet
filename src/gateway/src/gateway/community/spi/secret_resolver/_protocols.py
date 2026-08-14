"""SecretResolver SPI — abstract retrieval of a named secret from a credential backend.

Mirrors the backend ``agentclaw.community.plugin_api.secret_resolver.SecretResolver``
contract so the gateway can resolve signing keys (and other credentials) through
the same abstraction. Implementations are selected by deploy profile: community
reads from the process environment; enterprise may resolve from a corp secret
store / KMS. The gateway Protocol is a plain ``Protocol`` (gateway SPI
convention) rather than the backend ``Plugin`` base.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SecretResolver(Protocol):
    """Resolve a named secret to its credential object.

    Returns an object exposing ``.secret_user`` / ``.secret_value`` (duck-typed
    shape every consumer reads), or ``None`` when the backend has no such
    secret. Backend/transport errors are **not** swallowed — they propagate so
    callers that need a fallback must wrap the call in ``try/except``.
    """

    def get_secret(self, secret_name: str) -> Any | None:
        """Return the secret object for ``secret_name``, or ``None`` if absent."""
        ...
