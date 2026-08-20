"""SecretResolver SPI — abstract retrieval of a named secret from a credential backend.

Mirrors the backend ``agentclaw.community.plugin_api.secret_resolver.SecretResolver``
contract so the gateway can resolve signing keys (and other credentials) through
the same abstraction. Implementations are selected by deploy profile: community
reads from the process environment; enterprise may resolve from a corp secret
store / KMS. The gateway Protocol is a plain ``Protocol`` (gateway SPI
convention) rather than the backend ``Plugin`` base.

``get_secret`` MAY return either a duck-typed object exposing ``.secret_user`` /
``.secret_value`` (community flavor) or a plain ``str`` value (env flavor);
consumers must accept both shapes.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# Dev/test SM4 key — a non-production key used for local development and
# testing. Production deployments resolve the SM4 key from the active
# SecretResolver instead.
DEV_SM4_KEY = "rzq4b5aJpS62/FMDfK18Bw=="


@runtime_checkable
class SecretResolver(Protocol):
    """Resolve a named secret to its credential object.

    Returns an object exposing ``.secret_user`` / ``.secret_value`` (duck-typed
    shape), a plain ``str`` value, or ``None`` when the backend has no such
    secret. Backend/transport errors are **not** swallowed — they propagate so
    callers that need a fallback must wrap the call in ``try/except``.

    Implementations aligned with the BaaS ``SecretStorePlugin`` (the ``env``
    flavor) also provide ``resolve_secret``, ``get_kv_secret``,
    ``generate_proxy_token``, ``resolve_common_sm4_key``, and ``close``; the
    ``community`` flavor provides only ``get_secret``.
    """

    def get_secret(self, secret_name: str) -> Any | None:
        """Return the secret object for ``secret_name``, or ``None`` if absent."""
        ...
