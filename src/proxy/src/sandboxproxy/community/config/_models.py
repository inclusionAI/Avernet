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
    start: str = "sandboxproxy.community.adapters.web.app:app"
    enable_api_docs: bool = True


@dataclass
class ModuleConfig:
    web: WebConfig | None = None


@dataclass
class LogConfig:
    trace_log_dir: str = ""
    log_level: str = "INFO"
    log_dir: str = ""


class RouteConfig(BaseModel):
    prefix: str
    proxy_timeout: int = 86400
    websocket: bool = False


class CorsConfig(BaseModel):
    allowed_origins: list[str] = Field(default_factory=list)
    max_age: int = 3600


class JwtConfig(BaseModel):
    """JWT verification key config (community: config/env-driven, no mist)."""

    secret: str = ""


class AliyunAckClusterConfig(BaseModel):
    api_server: str = ""
    token: str = ""
    namespace: str = "default"

    @property
    def cluster_host(self) -> str:
        """Return the ACK API server host, stripping scheme for proxypass."""
        return self.api_server.removeprefix("https://").removeprefix("http://")


class ProxyConfig(BaseModel):
    """Proxy routing + middleware config (mirrors internal ``proxy`` section)."""

    middleware_chain: list[dict[str, Any]] = Field(default_factory=list)
    cors: CorsConfig = Field(default_factory=CorsConfig)
    routes: list[RouteConfig] = Field(default_factory=list)


class PluginConfig(BaseSettings):
    """Plugin selection config for the proxy DI container."""

    model_config = SettingsConfigDict(extra="allow")
    config_section: ClassVar[str] = "plugins"

    resolver: str = Field(default="prefix", min_length=1)
    relay_client: str = Field(default="baas", min_length=1)


class UserConfig(BaseModel):
    model_config = {"extra": "allow"}
    plugins: PluginConfig = Field(default_factory=PluginConfig)
    jwt: JwtConfig = Field(default_factory=JwtConfig)
    aliyun_ack_cluster: AliyunAckClusterConfig = Field(
        default_factory=AliyunAckClusterConfig
    )
    open_apis: dict[str, str] = Field(default_factory=dict)
    teclaw: dict[str, str] = Field(default_factory=dict)
    baas: dict[str, str] = Field(default_factory=dict)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            return default


@dataclass
class Config:
    app_name: str = "sandboxproxy"
    enable_sidecar: bool = False
    workers: int = 1
    log_config: LogConfig = field(default_factory=LogConfig)
    module_config: ModuleConfig = field(default_factory=ModuleConfig)
    user_config: UserConfig = field(default_factory=UserConfig)
    raw: dict[str, Any] = field(default_factory=dict)
    config_dir: Path | None = None
