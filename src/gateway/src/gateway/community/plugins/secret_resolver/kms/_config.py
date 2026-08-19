"""Configuration model for the Aliyun KMS secret resolver plugin.

Owned by the plugin layer (plugins may not import bootstrap), so the model
lives here and is imported by the bootstrap composition root.

Credential values (access key id/secret) are expected to come from secret
references (``@name``) resolved through the active ``SecretResolver`` in the
composition root, or from external configuration sources — never literals in
code or committed files.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class KmsSecretResolverConfig(BaseModel):
    """Aliyun KMS secret resolver connection configuration.

    Only used when ``plugins.secret`` is set to ``kms``.

    Sensitive fields (``access_key_id`` / ``access_key_secret``) may carry a
    secret reference (``@name``) that the composition root resolves before
    building the client; the resolver constructor only ever receives
    already-resolved values.
    """

    endpoint: str = Field(default="")
    region_id: str = Field(default="")
    access_key_id: str = Field(default="")
    access_key_secret: str = Field(default="")
    secret_name_prefix: str = Field(default="")
