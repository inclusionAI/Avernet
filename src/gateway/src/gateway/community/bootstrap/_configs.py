from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from ._container import ApplicationContainer


class ConfigError(Exception):
    pass


class ConfigKey(StrEnum):
    PLUGIN_FORWARDER = "plugins.forwarder"
    PLUGIN_SCHEMA_CATALOG = "plugins.schema_catalog"
    PLUGIN_CACHE = "plugins.cache"
    PLUGIN_AUTH = "plugins.auth"
    PLUGIN_AUTHN_APP_TOKEN = "plugins.authn.app_token"
    PLUGIN_AUTHN_TENANT = "plugins.authn.tenant"
    PLUGIN_DATABASE = "plugins.database"
    DATABASE_URL = "database.database_url"
    CREATE_SCHEMA = "database.create_schema"
    SEED_DATA = "database.seed_data"
    WEB_PORT = "web_port"


_CFG = SettingsConfigDict(extra="allow")

_CONFIG_SCHEMAS: dict[str, type[BaseSettings]] = {}


class ConfigSchema(BaseSettings):
    model_config = _CFG
    config_section: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        section = getattr(cls, "config_section", "")
        if section:
            _CONFIG_SCHEMAS[section] = cls


class CacheRedisConfig(ConfigSchema):
    """Redis cache plugin configuration. Only used when ``cache`` is ``redis``."""

    config_section = "cache_redis"
    url: str = "redis://localhost:6379/0"
    socket_timeout: float = Field(default=5.0, gt=0)
    socket_connect_timeout: float = Field(default=5.0, gt=0)


class DatabaseSectionConfig(ConfigSchema):
    """Top-level ``database`` section — connection params for the selected plugin.

    ``database_url`` is a full connection URL:
    - ``sqlite``: ``sqlite:///:memory:`` or ``sqlite:////tmp/secbaas.db``
    - ``mariadb``: ``mysql+aiomysql://user:pass@host:port/db?charset=utf8mb4``
    """

    config_section = "database"
    database_url: str = ""
    create_schema: bool = Field(default=False)
    seed_data: bool = Field(default=False)


class AuthnPluginConfig(BaseSettings):
    model_config = _CFG
    app_token: str = Field(default="stub")
    tenant: str = Field(default="stub")


class PluginConfig(ConfigSchema):
    """Plugin selection config for gateway DI container.

    Values are validated only for minimum length. The community
    package MUST NOT restrict which selectors enterprise can use —
    enterprise registrations inject additional provider options at
    bootstrap time through the ``plugin_registry`` module.
    """

    config_section = "plugins"
    forwarder: str = Field(default="httpx", min_length=1)
    schema_catalog: str = Field(default="file", min_length=1)
    cache: str = Field(default="stub", min_length=1)
    auth: str = Field(default="stub", min_length=1)
    secret: str = Field(default="community", min_length=1)
    authn: AuthnPluginConfig = Field(default_factory=AuthnPluginConfig)
    database: str = Field(default="sqlite", min_length=1)
    secret_aliyun_kms: dict = Field(default_factory=dict)


def _read_config(cfg, key: ConfigKey):
    parts = key.value.split(".")
    val = cfg
    for p in parts:
        try:
            if isinstance(val, dict):
                if p not in val:
                    raise ConfigError(
                        f"Config path segment '{p}' not found in '{key.value}'"
                    ) from None
                val = val[p]
            else:
                val = getattr(val, p)
        except AttributeError:
            raise ConfigError(
                f"Config path segment '{p}' not found in '{key.value}'"
            ) from None
    if callable(val) and not isinstance(val, dict):
        try:
            resolved = val()
        except Exception as exc:
            raise ConfigError(
                f"Config '{key.value}' is not set or failed to resolve"
            ) from exc
    else:
        resolved = val
    if resolved is None:
        raise ConfigError(f"Config '{key.value}' is not set")
    return resolved


def _schema_defaults() -> dict:
    return {
        section: schema().model_dump() for section, schema in _CONFIG_SCHEMAS.items()
    }


def load_container_config() -> dict:
    from gateway.community.config import ConfigLoader

    cfg = ConfigLoader.load()
    return _container_config_from_loaded(cfg)


def _container_config_from_loaded(cfg) -> dict:
    user_config = cfg.user_config.model_dump()
    if cfg.module_config.web:
        user_config[ConfigKey.WEB_PORT.value] = cfg.module_config.web.port
    return user_config


@dataclass
class DatabaseConfig:
    plugin_type: str
    db_url: str = ""
    create_schema: bool = False
    seed_data: bool = False


def init_container_config(container: "ApplicationContainer") -> None:
    from dependency_injector import providers

    from gateway.community.config import ConfigLoader

    loaded = ConfigLoader.load()
    config: providers.Configuration = container.config
    config.from_dict(_schema_defaults())
    config.from_dict(_container_config_from_loaded(loaded))
    container.loaded_config.override(providers.Object(loaded))
    container.user_config.override(providers.Object(loaded.user_config))
