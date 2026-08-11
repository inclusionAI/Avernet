"""Configuration model for the Aliyun KMS secret store plugin.

Owned by the plugin layer (plugins may not import bootstrap), so the model
lives here and is imported by the bootstrap composition root.

Credential values are expected to come from secret references (``@name``) or
external sources and are never literals in code.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class KmsSecretStoreConfig(BaseModel):
    """Aliyun KMS secret store plugin configuration.

    Only used when ``plugins.secret`` is set to ``aliyun_kms``.
    """

    endpoint: str = Field(default="")
    region_id: str = Field(default="")
    access_key_id: str = Field(default="")
    access_key_secret: str = Field(default="")

    sm4_key_secret_name: str = Field(default="")
    proxypass_secret_name: str = Field(default="")
    admin_token_secret_name: str = Field(default="")

    secret_name_prefix: str = Field(default="")
