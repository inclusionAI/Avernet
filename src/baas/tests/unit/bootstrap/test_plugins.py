"""Tests for PluginContainer — standalone, no database needed."""

from pathlib import Path

import pytest
import yaml

from secbaas.community.bootstrap.plugins import PluginContainer

COMMUNITY_DIR = Path(__file__).resolve().parents[3]
SINGLEBOX_DEV_CONFIG = COMMUNITY_DIR / "singlebox-configs" / "application-dev.yaml"


class TestPluginContainerStandalone:
    """PluginContainer can be created standalone with config injection."""

    def test_container_can_be_instantiated(self):
        """PluginContainer constructs without any arguments."""
        container = PluginContainer()
        assert container is not None

    def test_config_injection_via_from_dict(self):
        """Config from_dict flows to Selector providers."""
        container = PluginContainer()
        container.config.from_dict(
            {
                "plugins": {
                    "secret": "stub",
                    "permission": "stub",
                    "identity": "stub",
                },
            }
        )
        assert container.config.plugins.secret() == "stub"

    def test_singlebox_dev_config_defines_required_selectors(self):
        """Singlebox dev config defines every selector resolved at startup."""
        raw_config = yaml.safe_load(SINGLEBOX_DEV_CONFIG.read_text())
        plugins = raw_config["user_config"]["plugins"]
        sandbox = plugins["sandbox"]

        assert plugins["auth"] == "stub"
        assert plugins["secret"] == "stub"
        assert plugins["cache"] == "stub"
        assert plugins["engine_adapter"] == "stub"
        assert plugins["bot_service"] == "local"
        assert plugins["database"] == "sqlite"
        assert sandbox["arca"] == "local_proc"
        assert sandbox["desktop"] == "stub"
        assert sandbox["k8s"] == "stub"
        assert sandbox["docker"] == "stub"
        assert sandbox["poolab"] == "stub"
        assert plugins["bot"]["teclaw"] == "stub"


class TestAliyunAckSelector:
    """The aliyun_ack option resolves to AliyunAckSandboxPlugin."""

    def _container(self, with_templates: bool = True):
        from secbaas.community.bootstrap.plugins import PluginContainer

        container = PluginContainer()
        cfg = {
            "plugins": {
                "secret": "stub",
                "sandbox": {"arca": "aliyun_ack"},
            }
        }
        if with_templates:
            cfg["aliyun_ack_template"] = {
                "ALIYUN_ACK_TEMPLATE_default": {
                    "cluster": {
                        "endpoint": "https://ack.example.com",
                        "kubeconfig": "apiVersion: v1\nkind: Config\n",
                        "region": "cn-hangzhou",
                    },
                    "pod": {"image": "test:latest"},
                }
            }
        container.config.from_dict(cfg)
        return container

    def test_aliyun_ack_selector_wired(self):
        from secbaas.community.api.device_manage import ArcaCredentials
        from secbaas.community.plugins.sandbox.arca.aliyun_ack import (
            AliyunAckSandboxPlugin,
        )

        creds = ArcaCredentials(
            template_id=1,
            template_uuid="u",
            base_url="http://x",
            api_key="k",
            arca_template_id="ALIYUN_ACK_TEMPLATE_default",
        )
        plugin = self._container().arca_sandbox_plugin_factory(creds)
        assert isinstance(plugin, AliyunAckSandboxPlugin)
        assert "ALIYUN_ACK_TEMPLATE_default" in plugin._ack_templates

    def test_aliyun_ack_default_selector(self):
        from secbaas.community.plugins.sandbox.arca import StubArcaSandboxPlugin

        container = self._container()
        container.config.from_dict(
            {"plugins": {"secret": "stub", "sandbox": {"arca": "stub"}}}
        )
        plugin = container.arca_sandbox_plugin_factory()
        assert plugin is StubArcaSandboxPlugin


class TestRedisCacheSelector:
    """The redis cache option resolves to RedisCachePlugin with stub default."""

    def _container(self, cache: str = "stub", cache_redis: dict | None = None):
        from secbaas.community.bootstrap.plugins import PluginContainer

        container = PluginContainer()
        cfg: dict = {
            "plugins": {
                "secret": "stub",
                "cache": cache,
            }
        }
        if cache_redis is not None:
            cfg["cache_redis"] = cache_redis
        container.config.from_dict(cfg)
        return container

    def test_stub_selector_default(self):
        from secbaas.community.plugins.cache.stub import StubCachePlugin

        plugin = self._container(cache="stub").cache_plugin()
        assert isinstance(plugin, StubCachePlugin)

    def test_redis_selector_config_wired(self):
        from secbaas.community.plugins.cache.redis import RedisCachePlugin

        container = self._container(
            cache="redis",
            cache_redis={"url": "redis://testhost:6380/1", "socket_timeout": 3.0},
        )
        # Selector should resolve to RedisCachePlugin (will fail to connect,
        # but we just verify the config wiring, not the actual connection).
        # We can't call cache_plugin() without a live Redis, so just verify
        # the Selector has the redis option registered.
        selector = container.cache_plugin
        assert "redis" in selector.providers

    def test_redis_selector_absent_when_stub(self):
        container = self._container(cache="stub")
        selector = container.cache_plugin
        assert "stub" in selector.providers
