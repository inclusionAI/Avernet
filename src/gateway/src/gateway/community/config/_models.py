"""Configuration data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass
class WebConfig:
    port: int = 8888
    start: str = "gateway.community.adapters.web.app:app"
    enable_api_docs: bool = True


@dataclass
class ModuleConfig:
    web: WebConfig | None = None


@dataclass
class LogConfig:
    trace_log_dir: str = ""
    log_level: str = "INFO"
    log_dir: str = ""


class AuthnPluginConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="allow")
    app_token: str = Field(default="stub")
    tenant: str = Field(default="stub")


class DatabasePluginConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="allow")
    plugin_database: str = Field(default="SQLITE_ORM")
    database_url: str = ""


class SecretConfig(BaseModel):
    """SecretResolver configuration (mirrors backend ``CommunitySecretConfig``).

    ``env_prefix`` is the prefix the community (env-backed) SecretResolver
    prepends to ``{NAME}_VALUE`` / ``{NAME}_USER`` lookups. Enterprise overlays
    may swap the resolver implementation entirely via the plugin registry.
    """

    model_config = {"extra": "allow"}
    env_prefix: str = "AVERNET_SECRET_"


class PrincipalSignerPluginConfig(BaseModel):
    """Non-secret PrincipalSigner config — read from ``user_config.principal_signer``.

    The signing key (a secret) is resolved at runtime via the
    :class:`~gateway.community.spi.secret_resolver.SecretResolver` using
    ``secret_name``; ``kid`` / ``issuer`` / ``ttl_seconds`` are non-secret and
    live in the config file.
    """

    model_config = {"extra": "allow"}
    secret_name: str = "principal_signing_key"
    kid: str = "bare"
    issuer: str = "gateway"
    ttl_seconds: int = 60


class PluginConfig(BaseSettings):
    """Plugin selection config for gateway DI container.

    Values are validated only for minimum length. The community
    package MUST NOT restrict which selectors enterprise can use —
    enterprise registrations (auth, cache, database, etc.) inject
    additional provider options at bootstrap time through the
    ``plugin_registry`` module.
    """

    model_config = SettingsConfigDict(extra="allow")
    config_section: ClassVar[str] = "plugins"

    forwarder: str = Field(default="httpx", min_length=1)
    ws_forwarder: str = Field(default="websockets", min_length=1)
    schema_catalog: str = Field(default="file", min_length=1)
    cache: str = Field(default="stub", min_length=1)
    auth: str = Field(default="stub", min_length=1)
    secret: str = Field(default="community", min_length=1)
    authn: AuthnPluginConfig = Field(default_factory=AuthnPluginConfig)
    database: DatabasePluginConfig = Field(default_factory=DatabasePluginConfig)


class UserConfig(BaseModel):
    model_config = {"extra": "allow"}
    plugins: PluginConfig = Field(default_factory=PluginConfig)
    secret: SecretConfig = Field(default_factory=SecretConfig)
    principal_signer: PrincipalSignerPluginConfig = Field(
        default_factory=PrincipalSignerPluginConfig
    )
    upstream_vars: dict[str, str] = Field(default_factory=dict)
    identity_strategies: dict[str, list[str]] = Field(default_factory=dict)
    route_security: dict[str, dict[str, str]] = Field(default_factory=dict)
    upstreams: dict[str, Any] = Field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            return default

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)


@dataclass
class Config:
    app_name: str = "gateway"
    enable_sidecar: bool = False
    workers: int = 1
    log_config: LogConfig = field(default_factory=LogConfig)
    module_config: ModuleConfig = field(default_factory=ModuleConfig)
    user_config: UserConfig = field(default_factory=UserConfig)
    raw: dict = field(default_factory=dict)
    config_dir: Path | None = None
