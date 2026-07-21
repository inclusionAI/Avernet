"""Config key path constants and Pydantic schemas for application.yaml.

Each constant maps to a dotted config key read by the DI container
via ``container.config.xxx`` / ``providers.Configuration()``.
Pydantic models provide typed defaults with field-level validation.

Source: application.yaml -> user_config -> container.config.from_pydantic() + from_dict()
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from secbaas.community.spi.database import PluginDatabaseType

if TYPE_CHECKING:
    from ._container import ApplicationContainer


class ConfigError(Exception):
    """Raised when a required config value is missing or invalid."""


class ConfigKey(StrEnum):
    """All dotted config keys consumed by the DI container."""

    # Web server port — mapped from module_config.web.port via load_container_config()
    WEB_PORT = "web_port"

    # Plugin selectors (values: "real" | "stub")
    PLUGIN_CRYPTO = "plugins.crypto"
    PLUGIN_SECRET = "plugins.secret"
    PLUGIN_AUTH = "plugins.auth"
    PLUGIN_SCHEDULER = "plugins.scheduler"
    PLUGIN_CACHE = "plugins.cache"
    PLUGIN_SANDBOX_ARCA = "plugins.sandbox.arca"
    PLUGIN_SANDBOX_DESKTOP = "plugins.sandbox.desktop"
    PLUGIN_SANDBOX_TECLAW = "plugins.sandbox.teclaw"
    PLUGIN_SANDBOX_K8S = "plugins.sandbox.k8s"
    PLUGIN_SANDBOX_DOCKER = "plugins.sandbox.docker"

    # Database
    PLUGIN_DATABASE = "plugins.database.plugin_database"
    DATABASE_URL = "plugins.database.database_url"

    # Bot service (ClawBotService + BaasBotService)
    BOT_SERVICE_PROXY_BASE_URL = "bot_service.proxy_base_url"
    BOT_SERVICE_PROXY_WS_BASE_URL = "bot_service.proxy_ws_base_url"
    BOT_SERVICE_ADAPTER_PORT = "bot_service.adapter_port"
    BOT_SERVICE_CONNECT_TIMEOUT = "bot_service.connect_timeout"
    BOT_SERVICE_REQUEST_TIMEOUT = "bot_service.request_timeout"
    BOT_SERVICE_WS_PATH = "bot_service.ws_path"

    # Bot Health Checker
    HEALTH_CHECK_TIMEOUT = "bot_health_checker.health_check.timeout_seconds"
    HEALTH_CHECK_MAX_CONCURRENT = "bot_health_checker.health_check.max_concurrent"
    TTL_EXTEND_THRESHOLD = "bot_health_checker.ttl.extend_when_remaining_hours"
    TTL_TARGET = "bot_health_checker.ttl.target_ttl_hours"

    # Chat client pool
    CHAT_CLIENT_POOL_MAX_SIZE = "chat_client_pool.max_size"
    CHAT_CLIENT_POOL_MAX_CONNS_PER_SANDBOX = "chat_client_pool.max_conns_per_sandbox"
    CHAT_CLIENT_POOL_MAX_CONCURRENT_PER_CONN = (
        "chat_client_pool.max_concurrent_per_conn"
    )
    CHAT_CLIENT_POOL_SESSION_KEY_TIMEOUT = "chat_client_pool.session_key_timeout"
    CHAT_CLIENT_POOL_MAX_RETRIES = "chat_client_pool.max_retries"
    CHAT_CLIENT_POOL_RETRY_BASE_BACKOFF = "chat_client_pool.retry_base_backoff"

    # BotRunner task concurrency
    BOT_RUNNER_SOFTMAX = "bot_runner.softmax"
    BOT_RUNNER_PER_KEY_MAX = "bot_runner.per_key_max"
    BOT_RUNNER_QUEUE_MAX = "bot_runner.queue_max"
    BOT_RUNNER_ACQUIRE_TIMEOUT = "bot_runner.acquire_timeout"

    # Arca / sandbox
    ARCA_ENABLED = "arca.enabled"

    # BCN downlink protocol
    BCN_API_KEY_PREFIX = "bcn.api_key.prefix"

    # BCN uplink protocol
    BCN_UPLINK_BASE_URL = "bcn.uplink.base_url"
    BCN_UPLINK_PROVIDER_ID = "bcn.uplink.provider_id"


def _read_config(cfg, key: ConfigKey):
    """Traverse ``container.config`` by dotted key, raise if unset.

    Supports both ``Configuration`` proxy objects (``getattr`` access)
    and plain ``dict`` (key access).  The latter occurs when a
    ``Configuration`` provider is passed as a keyword argument to a
    ``Singleton`` / ``Factory`` — ``dependency_injector`` resolves it
    to a raw ``dict`` before invoking the factory.
    """
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
    # Configuration proxy values need to be called to resolve;
    # plain dict / str / int values are already resolved.
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


class EnvVar(StrEnum):
    """Environment variable names consumed at bootstrap time."""

    ENV_PLUGIN_DATABASE = "PLUGIN_DATABASE"
    DATABASE_URL = "DATABASE_URL"


# ═══════════════════════════════════════════════════════════════════════════
# Typed config schemas (Pydantic)
# ═══════════════════════════════════════════════════════════════════════════


_CFG = SettingsConfigDict(extra="allow")

_CONFIG_SCHEMAS: dict[str, type[BaseSettings]] = {}


class ConfigSchema(BaseSettings):
    """Base for all DI config schemas — auto-registers into ``_CONFIG_SCHEMAS``.

    Subclass and set ``config_section`` to the YAML key under ``user_config``.
    """

    model_config = _CFG
    config_section: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        section = getattr(cls, "config_section", "")
        if section:
            _CONFIG_SCHEMAS[section] = cls


class SandboxPluginConfig(BaseSettings):
    """Sandbox plugin selection — all stub by default (community-safe).

    Enterprise deployments override individual selectors via config overlay.
    """

    model_config = _CFG
    arca: str = Field(default="stub", pattern=r"^(arca_sdk|stub)$")
    desktop: str = Field(default="stub", pattern=r"^(real|stub)$")
    teclaw: str = Field(default="stub", pattern=r"^(real|stub)$")
    k8s: str = Field(default="stub", pattern=r"^(real|stub)$")
    docker: str = Field(default="stub", pattern=r"^(real|stub)$")
    poolab: str = Field(default="stub", pattern=r"^(real|stub)$")


class DatabasePluginConfig(BaseSettings):
    """Database plugin selection — SQLITE_ORM by default (community-safe)."""

    model_config = _CFG
    plugin_database: str = Field(default="SQLITE_ORM")
    database_url: str = ""


class PluginConfig(ConfigSchema):
    """Default plugin selection -- all stub by default."""

    config_section = "plugins"
    crypto: str = Field(default="stub", pattern=r"^(real|stub)$")
    secret: str = Field(default="stub", pattern=r"^(real|stub)$")
    auth: str = Field(default="stub", pattern=r"^(buservice|oauth|stub)$")
    scheduler: str = Field(default="stub", pattern=r"^(real|stub)$")
    cache: str = Field(default="stub", pattern=r"^(real|stub)$")
    bot_service: str = Field(default="stub", pattern=r"^(real|local|stub)$")

    engine_adapter: str = Field(default="stub", pattern=r"^(real|stub)$")
    file_transfer: str = Field(default="stub", pattern=r"^(real|stub)$")
    database: DatabasePluginConfig = Field(default_factory=DatabasePluginConfig)
    sandbox: SandboxPluginConfig = Field(default_factory=SandboxPluginConfig)


class FileTransferPollerConfigSchema(ConfigSchema):
    """File transfer poller configuration."""

    config_section = "file_transfer_poller"
    enabled: bool = Field(default=False)
    lock_expire_seconds: int = Field(default=300, ge=1)
    cron_interval_seconds: int = Field(default=10, ge=1)
    upload_timeout_seconds: int = Field(default=3600, ge=1)
    max_concurrent_tickets: int = Field(default=5, ge=1)
    dry_run: bool = Field(default=False)


class FileTransferOssConfigSchema(ConfigSchema):
    """OSS storage backend configuration for file transfer."""

    config_section = "file_transfer_oss"
    endpoint: str = Field(default="")
    bucket_name: str = Field(default="")
    staging_root_path: str = Field(default="baas-file-transfer")
    secret_name: str = Field(default="")


class BotServiceConfig(ConfigSchema):
    """Bot service connection parameters."""

    config_section = "bot_service"
    proxy_base_url: str = ""
    proxy_ws_base_url: str = ""
    adapter_port: int = Field(default=8080, ge=1, le=65535)
    connect_timeout: float = Field(default=10.0, gt=0)
    request_timeout: float = Field(default=30.0, gt=0)
    ws_path: str = "/ws"


class HealthCheckSettings(BaseSettings):
    """Health check parameters."""

    model_config = _CFG
    timeout_seconds: int = Field(default=10, ge=1)
    max_concurrent: int = Field(default=10, ge=1)


class TtlSettings(BaseSettings):
    """TTL extension parameters."""

    model_config = _CFG
    extend_when_remaining_hours: int = Field(default=16, ge=1)
    target_ttl_hours: int = Field(default=24, ge=1)


class BotHealthCheckerConfig(ConfigSchema):
    """Health check and TTL settings."""

    config_section = "bot_health_checker"
    health_check: HealthCheckSettings = Field(default_factory=HealthCheckSettings)
    ttl: TtlSettings = Field(default_factory=TtlSettings)


class ChatClientPoolConfig(ConfigSchema):
    """Chat client pool 和连接参数。"""

    config_section = "chat_client_pool"
    max_size: int = Field(default=100, ge=1)
    max_conns_per_sandbox: int = Field(default=2, ge=1)
    max_concurrent_per_conn: int = Field(default=0, ge=0)  # 0 = 不限
    session_key_timeout: float = Field(default=30.0, gt=0)  # 秒
    max_retries: int = Field(default=1, ge=0)  # 0 = 不重试
    retry_base_backoff: float = Field(default=0.5, gt=0)  # 秒


class BotRunnerConfig(ConfigSchema):
    """BotRunner 任务并发池配置"""

    config_section = "bot_runner"
    softmax: int = Field(default=1000, ge=0, description="全局最大并发任务数，0=不限")
    per_key_max: int = Field(
        default=2, ge=0, description="每个 bot_id 最大并发任务数，0=不限"
    )
    queue_max: int = Field(
        default=0,
        ge=0,
        description="全局最大排队等待数，0=不限；仅 queue 策略生效",
    )
    acquire_timeout: float = Field(
        default=0,
        ge=0,
        description="acquire 等待槽位的超时秒数，0=不限；仅 queue 策略生效；设为 0 依赖 task_timeout 保证队列流转",
    )
    task_timeout: float = Field(
        default=660.0,
        ge=0,
        description="单个任务最大执行秒数，默认 660（10分钟+1分钟缓冲）；0=不限；超时自动取消并释放槽位",
    )


class BcnUplinkConfigSchema(BaseModel):
    """BCN 上行协议客户端配置（application.yaml schema）"""

    base_url: str = ""
    provider_id: str = ""


class _BcnApiKeyConfig(BaseSettings):
    """BCN API key 配置。"""

    model_config = _CFG
    prefix: str = ""


class _BcnUplinkConfig(BaseSettings):
    """BCN 上行协议配置。"""

    model_config = _CFG
    base_url: str = ""
    provider_id: str = ""


class BcnConfig(ConfigSchema):
    """BCN 协议配置。"""

    config_section = "bcn"
    api_key: _BcnApiKeyConfig = Field(default_factory=_BcnApiKeyConfig)
    uplink: _BcnUplinkConfig = Field(default_factory=_BcnUplinkConfig)


class BotChatLogRelationConfig(ConfigSchema):
    """Bot chat log relation service 配置"""

    config_section = "bot_chat_log_relation"
    base_url: str = ""
    timeout: float = Field(default=10.0, gt=0)
    max_retries: int = Field(default=0, ge=0)  # 默认0，不重试


class BotRunQueueConfig(ConfigSchema):
    """BotRun 队列 Worker 配置"""

    config_section = "bot_run_queue"
    enabled: bool = False
    session_lock_expire_seconds: int = Field(default=300, ge=1)
    session_lock_renew_seconds: int = Field(default=60, ge=1)
    qpm_refresh_seconds: float = Field(default=30.0, gt=0)
    poll_interval_seconds: float = Field(default=0.1, gt=0)
    discover_limit: int = Field(default=50, ge=1)
    candidates_per_bot: int = Field(default=5, ge=1)
    max_concurrent: int = Field(default=50, ge=1)
    heartbeat_interval_seconds: float = Field(default=30.0, gt=0)
    machine_count: int = Field(default=1, ge=1)
    bucket_sweep_interval_seconds: float = Field(default=300.0, gt=0)
    bucket_idle_ttl_seconds: float = Field(default=600.0, gt=0)


def _schema_defaults() -> dict:
    """Build a dict of schema defaults keyed by config section name."""
    return {
        section: schema().model_dump() for section, schema in _CONFIG_SCHEMAS.items()
    }


def load_container_config() -> dict:
    """Load YAML config and build DI-ready user_config dict.

    Loads application config via ``ConfigLoader`` (overlay-aware), copies
    ``user_config``, and injects ``web_port`` from ``module_config.web.port``.
    Schema defaults are injected separately via ``init_container_config()``.
    """
    from secbaas.community.config import ConfigLoader

    cfg = ConfigLoader.load()
    user_config = dict(cfg.user_config)
    if cfg.module_config.web:
        user_config[ConfigKey.WEB_PORT.value] = cfg.module_config.web.port

    return user_config


@dataclass
class DatabaseConfig:
    """Configuration for database plugin initialisation."""

    plugin_type: PluginDatabaseType
    db_url: str = ""


def init_container_config(container: "ApplicationContainer") -> None:
    """Populate ``container.config`` with schema defaults then YAML overrides.

    Step 1: ``from_dict`` with schema defaults to seed typed values for every
    field (so absent YAML keys resolve to non-None).
    Step 2: ``from_dict`` with YAML values to override defaults (recursive merge).

    New config sections auto-register via ``ConfigSchema.__init_subclass__``.
    """
    from dependency_injector import providers

    config: providers.Configuration = container.config
    config.from_dict(_schema_defaults())
    config.from_dict(load_container_config())
