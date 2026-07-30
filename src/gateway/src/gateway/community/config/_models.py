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
    schema_catalog: str = Field(default="file", min_length=1)
    cache: str = Field(default="stub", min_length=1)
    auth: str = Field(default="stub", min_length=1)
    authn: AuthnPluginConfig = Field(default_factory=AuthnPluginConfig)
    database: DatabasePluginConfig = Field(default_factory=DatabasePluginConfig)


class UserConfig(BaseModel):
    model_config = {"extra": "allow"}
    plugins: PluginConfig = Field(default_factory=PluginConfig)

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
