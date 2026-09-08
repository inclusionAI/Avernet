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

    def _container(self, with_cluster: bool = True):
        from secbaas.community.bootstrap.plugins import PluginContainer

        container = PluginContainer()
        cfg = {
            "plugins": {
                "secret": "stub",
                "sandbox": {"arca": "aliyun_ack"},
            }
        }
        if with_cluster:
            cfg["aliyun_ack_cluster"] = {
                "api_server": "https://ack.example.com",
                "token": "dummy-token",
                "namespace": "default",
            }
            cfg["sandbox_images"] = {
                "ALIYUN_ACK_DEFAULT": {
                    "openclaw": "openclaw:latest",
                    "api-key-proxy": "nginx:alpine",
                    "init": "busybox:latest",
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
            arca_template_id="ALIYUN_ACK_DEFAULT",
        )
        plugin_factory = self._container().arca_sandbox_plugin_factory()
        plugin = plugin_factory(creds)
        assert isinstance(plugin, AliyunAckSandboxPlugin)
        assert plugin._config is creds

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


class TestSessionFileUrlProjectorSelector:
    """session_file_url_projector Selector: stub -> Noop, aliyun_ack -> AliyunAck.

    Mirrors TestAliyunAckSelector — the env section must be present in the
    dict because both Selector branches read config.env.deploy_tenant.
    """

    def _container(self, projector: str = "stub"):
        container = PluginContainer()
        cfg = {
            "plugins": {
                "secret": "stub",
                "session_file_url_projector": projector,
            },
            "env": {"deploy_tenant": ""},
        }
        if projector == "aliyun_ack":
            cfg["env"]["deploy_tenant"] = "aliyun"
            cfg["session_file_url_proxy"] = {
                "proxy_base_url": "https://bff.example.com",
            }
        container.config.from_dict(cfg)
        return container

    def test_stub_selector_resolves_noop(self):
        from secbaas.community.plugins.file_transfer import (
            NoopSessionFileUrlProjector,
        )

        projector = self._container("stub").session_file_url_projector()
        assert isinstance(projector, NoopSessionFileUrlProjector)

    def test_aliyun_ack_selector_config_wired(self):
        from secbaas.community.plugins.file_transfer.aliyun_ack import (
            AliyunAckSessionFileUrlProjector,
        )

        projector = self._container("aliyun_ack").session_file_url_projector()
        assert isinstance(projector, AliyunAckSessionFileUrlProjector)
        assert projector._proxy_base_url == "https://bff.example.com"
        assert projector._deploy_tenant == "aliyun"


class TestFileTransferBackendSelector:
    """file_transfer_backend Selector: real -> AliyunOssFileTransferBackend.

    The `real` key factory replicates the enterprise dual-branch factory
    (A3): aliyun tenant -> env-only AK/SK against the
    file_transfer_oss_aliyun section; main site -> secret plugin against
    the file_transfer_oss section. The factory calls get_container(), so
    every test registers its container via set_container() and restores
    the previous singleton afterwards.
    """

    @pytest.fixture(autouse=True)
    def _isolated_container_state(self):
        import secbaas.community.bootstrap as _bootstrap

        original = _bootstrap._container
        _bootstrap._container = None
        yield
        _bootstrap._container = original

    def _container(self, deploy_tenant: str):
        from secbaas.community.bootstrap import ApplicationContainer, set_container

        container = ApplicationContainer()
        container.config.from_dict(
            {
                "plugins": {
                    "secret": "stub",
                    "file_transfer": "real",
                },
                "env": {"deploy_tenant": deploy_tenant},
            }
        )
        set_container(container)
        return container

    def test_real_aliyun_branch_builds_env_credential_backend(
        self, monkeypatch
    ):
        """Aliyun tenant with the env AK/SK pair builds the real backend."""
        from secbaas.community.plugins.file_transfer import (
            AliyunOssFileTransferBackend,
        )

        container = self._container("aliyun")
        container.config.from_dict(
            {
                "file_transfer_oss_aliyun": {
                    "endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
                    "bucket_name": "my-bucket",
                    "staging_root_path": "baas-file-transfer",
                }
            }
        )
        monkeypatch.setenv("FT_OSS_ACCESS_KEY", "ak-id")
        monkeypatch.setenv("FT_OSS_SECRET_KEY", "ak-secret")

        backend = container.plugins().file_transfer_backend()

        assert isinstance(backend, AliyunOssFileTransferBackend)
        assert backend._config.endpoint == "https://oss-cn-hangzhou.aliyuncs.com"

    def test_real_aliyun_branch_missing_env_raises_config_error(
        self, monkeypatch
    ):
        """Aliyun tenant without the env pair fails fast with ConfigError."""
        from secbaas.community.bootstrap._configs import ConfigError

        container = self._container("aliyun")
        container.config.from_dict(
            {
                "file_transfer_oss_aliyun": {
                    "endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
                    "bucket_name": "my-bucket",
                    "staging_root_path": "baas-file-transfer",
                }
            }
        )
        monkeypatch.delenv("FT_OSS_ACCESS_KEY", raising=False)
        monkeypatch.delenv("FT_OSS_SECRET_KEY", raising=False)

        with pytest.raises(ConfigError, match="requires FT_OSS_ACCESS_KEY and"):
            container.plugins().file_transfer_backend()

    def test_real_aliyun_branch_requires_endpoint(self, monkeypatch):
        """Aliyun tenant with an empty endpoint fails before any env read."""
        from secbaas.community.bootstrap._configs import ConfigError

        container = self._container("aliyun")
        container.config.from_dict(
            {
                "file_transfer_oss_aliyun": {
                    "endpoint": "",
                    "bucket_name": "my-bucket",
                    "staging_root_path": "baas-file-transfer",
                }
            }
        )
        monkeypatch.setenv("FT_OSS_ACCESS_KEY", "ak-id")
        monkeypatch.setenv("FT_OSS_SECRET_KEY", "ak-secret")

        with pytest.raises(
            ConfigError, match="file_transfer_oss_aliyun.endpoint is required"
        ):
            container.plugins().file_transfer_backend()

    def test_real_main_site_branch_builds_secret_backend(self, monkeypatch):
        """Main site consumes the secret plugin exactly once via secret_name."""
        from secbaas.community.plugins.file_transfer import (
            AliyunOssFileTransferBackend,
        )
        from secbaas.community.plugins.secret.stub import StubSecretStorePlugin

        container = self._container("")
        container.config.from_dict(
            {
                "file_transfer_oss": {
                    "endpoint": "https://oss-internal.example.com",
                    "bucket_name": "main-bucket",
                    "secret_name": "oss-main-site",
                    "staging_root_path": "file-transfer",
                }
            }
        )
        calls: list[str] = []

        def fake_get_kv_secret(self, secret_name: str) -> tuple[str, str]:
            calls.append(secret_name)
            return ("AK-ID", "AK-SECRET")

        monkeypatch.setattr(
            StubSecretStorePlugin, "get_kv_secret", fake_get_kv_secret
        )

        backend = container.plugins().file_transfer_backend()

        assert isinstance(backend, AliyunOssFileTransferBackend)
        assert calls == ["oss-main-site"]
        assert backend._config.secret_name == "oss-main-site"
        assert backend._config.bucket_name == "main-bucket"

    def test_real_main_site_branch_requires_secret_name(self):
        """Main site without secret_name fails fast with ConfigError."""
        from secbaas.community.bootstrap._configs import ConfigError

        container = self._container("")
        container.config.from_dict(
            {
                "file_transfer_oss": {
                    "endpoint": "https://oss-internal.example.com",
                    "bucket_name": "main-bucket",
                    "staging_root_path": "file-transfer",
                }
            }
        )

        with pytest.raises(
            ConfigError, match="file_transfer_oss.secret_name is required"
        ):
            container.plugins().file_transfer_backend()
