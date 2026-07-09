"""Secret resolution protocol.

Abstracts retrieval of a named secret from the credential backend.
Implementations are selected by deploy profile: corp resolves from the
corp secret store; community resolves from environment variables; the
test/local impl is a no-op (every lookup yields ``None``).
"""

from typing import Any, Protocol, runtime_checkable
from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class SecretResolver(Plugin, Protocol):
    """Resolves a named secret to its credential object."""

    def get_secret(self, secret_name: str) -> Any | None:
        """Return the secret object (``.secret_user`` / ``.secret_value``)
        for ``secret_name``.

        Returns ``None`` only when the backend has no such secret (and
        in local mode, where the backend is unreachable and every
        lookup yields ``None``). Backend/transport errors are **not**
        swallowed — they propagate to the caller (faithful to the
        legacy ``core.dependencies.get_secret`` contract). Callers that
        need a fallback must wrap the call in ``try/except``."""
        ...
