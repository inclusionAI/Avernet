import time

from secbaas.plugins.cache.stub import StubCachePlugin


class TestStubCachePlugin:
    def setup_method(self):
        self.plugin = StubCachePlugin()

    def test_set_and_get(self):
        self.plugin.set("k1", "v1", ttl_seconds=60)
        assert self.plugin.get("k1") == "v1"

    def test_get_missing_key(self):
        assert self.plugin.get("nonexistent") is None

    def test_set_overwrites(self):
        self.plugin.set("k1", "v1", ttl_seconds=60)
        self.plugin.set("k1", "v2", ttl_seconds=60)
        assert self.plugin.get("k1") == "v2"

    def test_clear(self):
        self.plugin.set("k", "v", ttl_seconds=60)
        self.plugin.clear()
        assert self.plugin.get("k") is None

    def test_different_keys(self):
        self.plugin.set("a", "1", ttl_seconds=60)
        self.plugin.set("b", "2", ttl_seconds=60)
        assert self.plugin.get("a") == "1"
        assert self.plugin.get("b") == "2"

    def test_ttl_not_expired(self):
        self.plugin.set("k", "v", ttl_seconds=60)
        assert self.plugin.get("k") == "v"

    def test_ttl_expiry(self):
        self.plugin.set("k_short", "v", ttl_seconds=1)
        assert self.plugin.get("k_short") == "v"
        time.sleep(1.1)
        assert self.plugin.get("k_short") is None

    def test_empty_string_value(self):
        self.plugin.set("k", "", ttl_seconds=60)
        assert self.plugin.get("k") == ""

    def test_clear_on_empty(self):
        self.plugin.clear()  # should not raise
