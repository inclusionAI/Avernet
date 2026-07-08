"""Tests for InfraContainer — standalone, no database needed.

Config-based providers (bot_service_config, baas_bot_service_config) are
testable with config.from_dict.  The chat_client_pool resolves without any
dependencies.  Providers that require MOSN/Layotto (claw_bot_service,
baas_bot_service, etc.) are verified structurally — they exist and accept
overrides.
"""

from unittest.mock import MagicMock

import pytest

from secbaas.bootstrap._core_services import CoreServiceContainer as InfraContainer
from secbaas.core.service.bot_run import AsyncChatClientPool


class TestInfraContainerStandalone:
    """InfraContainer can be created standalone with mocked deps."""

    def test_container_can_be_instantiated(self):
        """InfraContainer constructs, even without wiring deps."""
        container = InfraContainer()
        assert container is not None

    def test_config_key_paths_are_accessible(self):
        """Config injection via from_dict reads bot_service paths."""
        container = InfraContainer()
        container.config.from_dict(
            {
                "bot_service": {
                    "proxy_base_url": "http://proxy:8080",
                    "proxy_ws_base_url": "ws://proxy:8080",
                    "adapter_port": 20003,
                    "connect_timeout": 5,
                    "request_timeout": 15,
                },
            }
        )
        assert container.config.bot_service.proxy_base_url() == "http://proxy:8080"
        assert container.config.bot_service.adapter_port() == 20003

    def test_all_provider_attributes_exist(self):
        """All 9 infra provider attributes are present."""
        container = InfraContainer()
        expected = [
            "chat_client_pool",
            "device_template_service",
            "paas_facade",
            "bot_service_config",
            "baas_bot_service_config",
            "bot_wss_dispatcher",
            "bot_binding_resolver",
            "claw_bot_service",
            "baas_bot_service",
        ]
        for attr in expected:
            assert hasattr(container, attr), f"Missing provider: {attr}"

    def test_chat_client_pool_resolves_without_deps(self):
        """AsyncChatClientPool does not require any Dependency()."""
        container = InfraContainer()
        pool = container.chat_client_pool()
        assert isinstance(pool, AsyncChatClientPool)

    def test_chat_client_pool_is_singleton(self):
        """AsyncChatClientPool is a singleton — same instance returned."""
        container = InfraContainer()
        pool1 = container.chat_client_pool()
        pool2 = container.chat_client_pool()
        assert pool1 is pool2

    def test_bot_service_config_resolves_with_config(self):
        """bot_service_config resolves when config is set."""
        container = InfraContainer()
        container.config.from_dict(
            {
                "bot_service": {
                    "proxy_base_url": "http://proxy:9090",
                    "proxy_ws_base_url": "ws://proxy:9090",
                    "adapter_port": 20003,
                    "connect_timeout": 10,
                    "request_timeout": 30,
                },
            }
        )
        cfg = container.bot_service_config()
        assert cfg.proxy_base_url == "http://proxy:9090"
        assert cfg.adapter_port == 20003

    def test_baas_bot_service_config_resolves_with_config(self):
        """baas_bot_service_config resolves when config is set."""
        container = InfraContainer()
        container.config.from_dict(
            {
                "bot_service": {
                    "adapter_port": 20003,
                    "ws_path": "/api/ws",
                    "connect_timeout": 5,
                    "request_timeout": 20,
                },
            }
        )
        cfg = container.baas_bot_service_config()
        assert cfg.adapter_port == 20003
        assert cfg.ws_path == "/api/ws"
        assert cfg.connect_timeout == 5

    def test_resolve_with_mocked_deps(self):
        """Infra providers accept overridden repo dependencies."""
        container = InfraContainer()
        container.config.from_dict(
            {
                "bot_service": {
                    "proxy_base_url": "http://proxy:9090",
                    "proxy_ws_base_url": "ws://proxy:9090",
                    "adapter_port": 20003,
                    "connect_timeout": 10,
                    "request_timeout": 30,
                },
            }
        )

        mock_repo = MagicMock()
        container.device_template_repo.override(mock_repo)
        container.bot_repo.override(mock_repo)
        container.device_repo.override(mock_repo)
        container.ac_bot_repo.override(mock_repo)
        container.ac_bot_publish_repo.override(mock_repo)
        container.device_binding_repo.override(mock_repo)

        store = container.chat_client_pool()
        assert isinstance(store, AsyncChatClientPool)
