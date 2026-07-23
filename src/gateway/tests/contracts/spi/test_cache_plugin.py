import pytest

from gateway.community.plugins.cache.bare._plugin import BareCachePlugin
from gateway.community.spi.cache import CachePlugin


class CachePluginContract:
    plugin: CachePlugin

    def test_set_and_get_round_trips(self) -> None:
        self.plugin.set("k1", "v1", ttl_seconds=60)
        assert self.plugin.get("k1") == "v1"

    def test_get_missing_key_returns_none(self) -> None:
        assert self.plugin.get("nonexistent_key") is None

    def test_set_overwrites_existing(self) -> None:
        self.plugin.set("k1", "v1", ttl_seconds=60)
        self.plugin.set("k1", "v2", ttl_seconds=60)
        assert self.plugin.get("k1") == "v2"

    def test_get_returns_string(self) -> None:
        self.plugin.set("k1", "12345", ttl_seconds=60)
        assert isinstance(self.plugin.get("k1"), str)

    def test_empty_string_value(self) -> None:
        self.plugin.set("k1", "", ttl_seconds=60)
        assert self.plugin.get("k1") == ""

    def test_close_clears_store(self) -> None:
        self.plugin.set("k1", "v1", ttl_seconds=60)
        self.plugin.close()
        assert self.plugin.get("k1") is None


class TestBareCachePlugin(CachePluginContract):
    def setup_method(self) -> None:
        self.plugin = BareCachePlugin()

    def test_ttl_expiry(self) -> None:
        import time

        self.plugin.set("k_short", "v", ttl_seconds=1)
        assert self.plugin.get("k_short") == "v"
        time.sleep(1.1)
        assert self.plugin.get("k_short") is None
