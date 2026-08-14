"""CommunitySecretResolver — community secret resolution from the environment.

Mirrors the backend ``agentclaw.community.plugins.community.secret_resolver``
implementation: a real, dependency-free resolver that reads two env vars per
secret — ``{env_prefix}{NAME}_USER`` and ``{env_prefix}{NAME}_VALUE`` — and
returns an object exposing ``.secret_user`` / ``.secret_value``. When the value
var is unset the secret is absent and the lookup returns ``None``, preserving
the Protocol's "absent ⇒ None" contract.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from gateway.community.spi.secret_resolver import SecretResolver


@dataclass(frozen=True)
class _EnvSecret:
    """Resolved secret — the ``.secret_user`` / ``.secret_value`` surface
    consumers read off the corp secret object."""

    secret_user: str
    secret_value: str


class CommunitySecretResolver(SecretResolver):
    """Resolve a named secret from two prefixed environment variables."""

    def __init__(self, env_prefix: str) -> None:
        self._prefix = env_prefix

    def get_secret(self, secret_name: str) -> _EnvSecret | None:
        norm = secret_name.upper().replace("-", "_")
        value = os.environ.get(f"{self._prefix}{norm}_VALUE")
        if value is None:
            # Absent — the credential's value is unset, so the secret does not
            # exist. Returning None (never a half-populated object with an empty
            # value) preserves the Protocol's "absent ⇒ None" contract, so a
            # consumer that branches on ``secret is not None`` falls back rather
            # than running with an empty credential. ``_USER`` is secondary — a
            # token-only secret (value set, no user) resolves with user="".
            return None
        user = os.environ.get(f"{self._prefix}{norm}_USER")
        return _EnvSecret(secret_user=user or "", secret_value=value)
