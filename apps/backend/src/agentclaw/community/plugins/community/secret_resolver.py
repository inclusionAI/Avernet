"""CommunitySecretResolver — community secret resolution from the environment.

The corp ``SecretResolver`` resolves a named credential from the corp secret
store. A community deployment has no such service; the natural, dependency-free
credential source is the process environment. ``get_secret(name)`` reads one
env var — the secret name itself — and returns an object exposing
``.secret_user`` / ``.secret_value`` (the duck-typed shape every consumer
already reads). When the env var is not set a ``KeyError`` is raised, so a
missing secret fails loudly at the point of use rather than silently
returning ``None``.

A real, deployable implementation (not a ``MockSeam`` test double).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from agentclaw.community.plugin_api.secret_resolver import SecretResolver


@dataclass(frozen=True)
class _EnvSecret:
    """Resolved secret — the ``.secret_user`` / ``.secret_value`` surface
    consumers read off the corp secret object."""

    secret_user: str
    secret_value: str


class CommunitySecretResolver(SecretResolver):
    """Resolve a named secret from the environment variable of the same name."""

    def __init__(self, env_prefix: str) -> None:
        self._prefix = env_prefix

    def get_secret(self, secret_name: str) -> _EnvSecret | None:
        key = f"{self._prefix}{secret_name}"
        value = os.environ.get(key)
        if value is None:
            return None
        return _EnvSecret(secret_user="", secret_value=value)
