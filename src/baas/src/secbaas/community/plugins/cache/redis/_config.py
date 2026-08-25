"""Configuration model for the baas Redis cache plugin.

Owned by the plugin layer (plugins may not import bootstrap), so the model
lives here and is imported by the bootstrap composition root.

Credential values (password) are expected to come from secret references
(``@name``) resolved through the active ``SecretStorePlugin`` in the composition
root, or from external configuration sources — never literals in code or
committed files.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

_REDIS_SOCKET_TIMEOUT = 5.0


class RedisCacheConfig(BaseModel):
    """Redis cache plugin connection configuration.

    Only used when ``plugins.cache`` is set to ``redis``.

    Sensitive fields (``password``) may carry a secret reference (``@name``)
    that the composition root resolves before constructing the client; the
    ``RedisCachePlugin`` constructor only ever receives already-resolved values.
    """

    host: str = Field(default="127.0.0.1")
    port: int = Field(default=6379)
    password: str = Field(default="")
    username: str = Field(default="")
    db: int = Field(default=0)
    ssl: bool = Field(default=False)
    socket_timeout: float = Field(default=_REDIS_SOCKET_TIMEOUT)
