"""Tests for the configs module — constant values, dataclass schemas, and helpers."""

import pytest

from secbaas.bootstrap._configs import (
    BotHealthCheckerConfig,
    BotServiceConfig,
    ChatClientPoolConfig,
    ConfigKey,
    EnvVar,
    PluginConfig,
)


class TestConfigKeyConstants:
    """Config key string constants are well-formed dotted paths."""

    @pytest.mark.parametrize(
        "key, expected",
        [
            (ConfigKey.PLUGIN_CRYPTO, "plugins.crypto"),
            (ConfigKey.PLUGIN_SECRET, "plugins.secret"),
            (ConfigKey.PLUGIN_AUTH, "plugins.auth"),
            (ConfigKey.PLUGIN_SCHEDULER, "plugins.scheduler"),
            (ConfigKey.PLUGIN_CACHE, "plugins.cache"),
            (ConfigKey.PLUGIN_DATABASE, "plugins.database.plugin_database"),
            (ConfigKey.BOT_SERVICE_PROXY_BASE_URL, "bot_service.proxy_base_url"),
            (ConfigKey.BOT_SERVICE_PROXY_WS_BASE_URL, "bot_service.proxy_ws_base_url"),
            (ConfigKey.BOT_SERVICE_ADAPTER_PORT, "bot_service.adapter_port"),
            (ConfigKey.BOT_SERVICE_CONNECT_TIMEOUT, "bot_service.connect_timeout"),
            (ConfigKey.BOT_SERVICE_REQUEST_TIMEOUT, "bot_service.request_timeout"),
            (
                ConfigKey.HEALTH_CHECK_TIMEOUT,
                "bot_health_checker.health_check.timeout_seconds",
            ),
            (
                ConfigKey.HEALTH_CHECK_MAX_CONCURRENT,
                "bot_health_checker.health_check.max_concurrent",
            ),
            (
                ConfigKey.TTL_EXTEND_THRESHOLD,
                "bot_health_checker.ttl.extend_when_remaining_hours",
            ),
            (ConfigKey.TTL_TARGET, "bot_health_checker.ttl.target_ttl_hours"),
            (ConfigKey.ARCA_ENABLED, "arca.enabled"),
            (
                ConfigKey.CHAT_CLIENT_POOL_MAX_SIZE,
                "chat_client_pool.max_size",
            ),
            (
                ConfigKey.CHAT_CLIENT_POOL_MAX_CONNS_PER_SANDBOX,
                "chat_client_pool.max_conns_per_sandbox",
            ),
            (
                ConfigKey.CHAT_CLIENT_POOL_MAX_CONCURRENT_PER_CONN,
                "chat_client_pool.max_concurrent_per_conn",
            ),
            (
                ConfigKey.CHAT_CLIENT_POOL_SESSION_KEY_TIMEOUT,
                "chat_client_pool.session_key_timeout",
            ),
            (
                ConfigKey.CHAT_CLIENT_POOL_MAX_RETRIES,
                "chat_client_pool.max_retries",
            ),
            (
                ConfigKey.CHAT_CLIENT_POOL_RETRY_BASE_BACKOFF,
                "chat_client_pool.retry_base_backoff",
            ),
        ],
    )
    def test_config_key_values(self, key, expected):
        assert key.value == expected

    # ── Non-enum constants ────────────────────────────────────────────────

    def test_plugin_database_env_var(self):
        assert EnvVar.ENV_PLUGIN_DATABASE == "PLUGIN_DATABASE"

    def test_env_var_is_member(self):
        assert "PLUGIN_DATABASE" in list(EnvVar)


class TestPluginConfigDataclass:
    """PluginConfig dataclass defaults and field overrides."""

    def test_defaults_all_stub(self):
        cfg = PluginConfig()
        assert cfg.crypto == "stub"
        assert cfg.secret == "stub"
        assert cfg.auth == "stub"
        assert cfg.scheduler == "stub"
        # engine_adapter 默认 stub —— 缺失该键的配置(如 singlebox)靠此默认兜底,
        # 否则 bootstrap 的 providers.Selector(config.plugins.engine_adapter) 装配失败。
        assert cfg.engine_adapter == "stub"

    def test_partial_override(self):
        cfg = PluginConfig(crypto="real")
        assert cfg.crypto == "real"
        assert cfg.secret == "stub"

    def test_full_override(self):
        cfg = PluginConfig(
            crypto="real",
            secret="real",
            auth="buservice",
            scheduler="real",
            engine_adapter="real",
        )
        assert cfg.auth == "buservice"
        assert cfg.scheduler == "real"
        assert cfg.engine_adapter == "real"

    def test_engine_adapter_invalid_value_rejected(self):
        # pattern=r"^(real|stub)$" —— 非法值须被 pydantic 拒绝
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            PluginConfig(engine_adapter="local")

    def test_engine_adapter_in_schema_defaults(self):
        # _schema_defaults() 在 init_container_config 第一步播种,保证 YAML 缺
        # engine_adapter 键时(如 singlebox-configs)config.plugins.engine_adapter
        # 仍解析为 "stub",bootstrap 的 providers.Selector 不会拿到 None。
        from secbaas.bootstrap._configs import _schema_defaults

        plugins_defaults = _schema_defaults()["plugins"]
        assert plugins_defaults["engine_adapter"] == "stub"


class TestBotServiceConfigDataclass:
    """BotServiceConfig dataclass defaults and field overrides."""

    def test_defaults(self):
        cfg = BotServiceConfig()
        assert cfg.proxy_base_url == ""
        assert cfg.proxy_ws_base_url == ""
        assert cfg.adapter_port == 8080
        assert cfg.connect_timeout == 10.0
        assert cfg.request_timeout == 30.0
        assert cfg.ws_path == "/ws"

    def test_partial_override(self):
        cfg = BotServiceConfig(proxy_base_url="http://proxy:9090", adapter_port=20003)
        assert cfg.proxy_base_url == "http://proxy:9090"
        assert cfg.adapter_port == 20003
        assert cfg.ws_path == "/ws"

    def test_full_override(self):
        cfg = BotServiceConfig(
            proxy_base_url="http://proxy:9090",
            proxy_ws_base_url="ws://proxy:9090",
            adapter_port=20003,
            connect_timeout=5.0,
            request_timeout=15.0,
            ws_path="/custom/ws",
        )
        assert cfg.adapter_port == 20003
        assert cfg.connect_timeout == 5.0
        assert cfg.ws_path == "/custom/ws"


class TestBotHealthCheckerConfigDataclass:
    """BotHealthCheckerConfig dataclass defaults and field overrides."""

    def test_defaults(self):
        cfg = BotHealthCheckerConfig()
        assert cfg.health_check.timeout_seconds == 10
        assert cfg.health_check.max_concurrent == 10
        assert cfg.ttl.extend_when_remaining_hours == 16
        assert cfg.ttl.target_ttl_hours == 24

    def test_partial_override(self):
        cfg = BotHealthCheckerConfig(
            health_check={"timeout_seconds": 30},
            ttl={"target_ttl_hours": 48},
        )
        assert cfg.health_check.timeout_seconds == 30
        assert cfg.health_check.max_concurrent == 10
        assert cfg.ttl.target_ttl_hours == 48

    def test_full_override(self):
        cfg = BotHealthCheckerConfig(
            health_check={"timeout_seconds": 5, "max_concurrent": 20},
            ttl={"extend_when_remaining_hours": 8, "target_ttl_hours": 12},
        )
        assert cfg.health_check.timeout_seconds == 5
        assert cfg.health_check.max_concurrent == 20
        assert cfg.ttl.extend_when_remaining_hours == 8
        assert cfg.ttl.target_ttl_hours == 12


class TestChatClientPoolConfigDataclass:
    """ChatClientPoolConfig dataclass defaults and field overrides."""

    def test_defaults(self):
        cfg = ChatClientPoolConfig()
        assert cfg.max_size == 100
        assert cfg.max_conns_per_sandbox == 2
        assert cfg.max_concurrent_per_conn == 0
        assert cfg.session_key_timeout == 30.0
        assert cfg.max_retries == 1
        assert cfg.retry_base_backoff == 0.5

    def test_partial_override(self):
        cfg = ChatClientPoolConfig(max_concurrent_per_conn=5, session_key_timeout=10.0)
        assert cfg.max_concurrent_per_conn == 5
        assert cfg.session_key_timeout == 10.0
        assert cfg.max_size == 100  # 默认值不变

    def test_full_override(self):
        cfg = ChatClientPoolConfig(
            max_size=50,
            max_conns_per_sandbox=3,
            max_concurrent_per_conn=10,
            session_key_timeout=15.0,
            max_retries=3,
            retry_base_backoff=1.0,
        )
        assert cfg.max_size == 50
        assert cfg.max_conns_per_sandbox == 3
        assert cfg.max_concurrent_per_conn == 10
        assert cfg.session_key_timeout == 15.0
        assert cfg.max_retries == 3
        assert cfg.retry_base_backoff == 1.0

    def test_validation_max_concurrent_per_conn_non_negative(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            ChatClientPoolConfig(max_concurrent_per_conn=-1)

    def test_validation_session_key_timeout_positive(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            ChatClientPoolConfig(session_key_timeout=0)

    def test_validation_max_retries_non_negative(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            ChatClientPoolConfig(max_retries=-1)

    def test_validation_retry_base_backoff_positive(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            ChatClientPoolConfig(retry_base_backoff=0)
