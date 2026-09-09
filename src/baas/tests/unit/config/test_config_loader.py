"""Tests for ConfigLoader — config loading with optional overlay."""

import pytest


class TestResolvePath:
    """Tests for ConfigLoader._resolve_overlay_path()."""

    def test_custom_overlay_name(self):
        from secbaas.community.config import ConfigLoader

        path = ConfigLoader._resolve_overlay_path("redis", "configs")
        assert path == "configs/overlays/redis.yaml"

    def test_custom_overlay_with_different_config_dir(self):
        from secbaas.community.config import ConfigLoader

        path = ConfigLoader._resolve_overlay_path("custom-feature", "/app/conf")
        assert path == "/app/conf/overlays/custom-feature.yaml"


class TestLoad:
    """Tests for ConfigLoader.load()."""

    def test_overlay_loads_from_yaml(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base_app\nworkers: 2\nuser_config: {}\n"
        )
        overlay_dir = config_dir / "overlays"
        overlay_dir.mkdir()
        (overlay_dir / "custom.yaml").write_text(
            "app_name: custom_app\nuser_config:\n  key: val\n"
        )
        monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "custom")
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.app_name == "custom_app"
        assert config.user_config["key"] == "val"

    def test_not_set_falls_back_to_base_yaml(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: default_app\nworkers: 1\nuser_config: {}\n"
        )
        # Use monkeypatch so SOFAPY_CONFIG_PATH is restored after the test,
        # avoiding leaking config-path state into subsequent tests.
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.app_name == "default_app"
        assert config.workers == 1

    def test_overlay_file_not_found_raises_error(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base_app\nworkers: 2\nuser_config: {}\n"
        )
        monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "nonexistent")
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        from secbaas.community.config import ConfigLoader

        with pytest.raises(
            FileNotFoundError, match="SOFAPY_CONFIG_OVERLAY=nonexistent"
        ):
            ConfigLoader.load()

    def test_overlay_merges_with_base_config(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 2\nuser_config:\n  base_key: base_value\n"
        )
        overlay_dir = config_dir / "overlays"
        overlay_dir.mkdir()
        (overlay_dir / "override.yaml").write_text(
            "workers: 8\nuser_config:\n  extra_key: extra_value\n"
        )
        monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "override")
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.app_name == "base"
        assert config.workers == 8
        assert config.user_config["base_key"] == "base_value"
        assert config.user_config["extra_key"] == "extra_value"


class TestEnvOverlaySelection:
    """Tests for the application-<env>.yaml overlay name selection.

    COMMUNITY_DEPLOY, when set, names the overlay and wins over SERVER_ENV.
    """

    def test_server_env_selects_env_overlay(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 1\nuser_config: {}\n"
        )
        (config_dir / "application-dev.yaml").write_text("workers: 4\n")
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("COMMUNITY_DEPLOY", raising=False)
        monkeypatch.setenv("SERVER_ENV", "dev")
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.app_name == "base"
        assert config.workers == 4

    def test_community_deploy_wins_over_server_env(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 1\nuser_config: {}\n"
        )
        # Both overlays exist: COMMUNITY_DEPLOY must pick community, not prod.
        (config_dir / "application-prod.yaml").write_text("workers: 5\n")
        (config_dir / "application-community.yaml").write_text("workers: 6\n")
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.setenv("SERVER_ENV", "prod")
        monkeypatch.setenv("COMMUNITY_DEPLOY", "community")
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.workers == 6

    def test_community_deploy_without_server_env(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 1\nuser_config: {}\n"
        )
        (config_dir / "application-community.yaml").write_text("workers: 6\n")
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.setenv("COMMUNITY_DEPLOY", "community")
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.workers == 6

    def test_community_deploy_missing_file_falls_back_to_base(
        self, monkeypatch, tmp_path
    ):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 1\nuser_config: {}\n"
        )
        # No application-community.yaml: the missing overlay is ignored.
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.setenv("COMMUNITY_DEPLOY", "community")
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.workers == 1

    def test_server_env_missing_file_falls_back_to_base(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 1\nuser_config: {}\n"
        )
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("COMMUNITY_DEPLOY", raising=False)
        monkeypatch.setenv("SERVER_ENV", "nonexistent")
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.workers == 1

    def test_no_env_vars_loads_base_only(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 1\nuser_config: {}\n"
        )
        (config_dir / "application-dev.yaml").write_text("workers: 4\n")
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.delenv("COMMUNITY_DEPLOY", raising=False)
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.workers == 1


class TestDeployTenantOverlay:
    """Tests for the BAAS_DEPLOY_TENANT tenant overlay layer.

    Merged after the SERVER_ENV/COMMUNITY_DEPLOY layer; a missing file is
    silently skipped; an unset var keeps the legacy behaviour byte-identical.
    """

    def test_tenant_overlay_merges_after_env_layer(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 1\nuser_config: {}\n"
        )
        (config_dir / "application-dev.yaml").write_text("workers: 4\n")
        (config_dir / "application-aliyun.yaml").write_text("workers: 9\n")
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("COMMUNITY_DEPLOY", raising=False)
        monkeypatch.setenv("SERVER_ENV", "dev")
        monkeypatch.setenv("BAAS_DEPLOY_TENANT", "aliyun")
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.app_name == "base"
        # Tenant layer is merged last — its value wins over the env layer.
        assert config.workers == 9

    def test_tenant_overlay_merges_without_env_layer(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 1\nuser_config: {}\n"
        )
        (config_dir / "application-aliyun.yaml").write_text("workers: 9\n")
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.delenv("COMMUNITY_DEPLOY", raising=False)
        monkeypatch.setenv("BAAS_DEPLOY_TENANT", "aliyun")
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.app_name == "base"
        assert config.workers == 9

    def test_tenant_overlay_missing_file_silently_skipped(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 1\nuser_config: {}\n"
        )
        # No application-aliyun.yaml: the missing tenant overlay is ignored.
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.delenv("COMMUNITY_DEPLOY", raising=False)
        monkeypatch.setenv("BAAS_DEPLOY_TENANT", "aliyun")
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.workers == 1
        assert config.app_name == "base"

    def test_tenant_env_unset_keeps_legacy_behavior(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 1\nuser_config: {}\n"
        )
        (config_dir / "application-dev.yaml").write_text("workers: 4\n")
        # No BAAS_DEPLOY_TENANT at all: behaviour identical to today.
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("COMMUNITY_DEPLOY", raising=False)
        monkeypatch.delenv("BAAS_DEPLOY_TENANT", raising=False)
        monkeypatch.setenv("SERVER_ENV", "dev")
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.app_name == "base"
        assert config.workers == 4

    @pytest.mark.parametrize(
        ("tenant_value", "bait_files"),
        [
            # Charset violation: a valid Linux filename the guard must reject.
            ("ali^yun", {"application-ali^yun.yaml": "workers: 99\n"}),
            # Path traversal: an unguarded join resolves
            # configs/application-../../evil.yaml to configs/evil.yaml, so
            # plant the literal "application-.." directory plus the evil.yaml
            # bait — a guard-deleted loader would merge workers: 99.
            ("../../evil", {"application-..": None, "evil.yaml": "workers: 99\n"}),
        ],
    )
    def test_tenant_value_charset_guard(
        self, monkeypatch, tmp_path, tenant_value, bait_files
    ):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 1\nuser_config: {}\n"
        )
        # Plant the exact files an UNGUARDED loader needs to load this tenant
        # overlay: without the charset guard the bait merges and flips workers
        # to 99, so these assertions pin the guard — not just "no crash".
        for rel_path, content in bait_files.items():
            target = config_dir / rel_path
            if content is None:
                target.mkdir()
            else:
                target.write_text(content)
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.delenv("COMMUNITY_DEPLOY", raising=False)
        monkeypatch.setenv("BAAS_DEPLOY_TENANT", tenant_value)
        from secbaas.community.config import ConfigLoader

        # The invalid tenant is rejected no matter what bait is planted:
        # no filesystem escape, no overlay load — base values survive.
        config = ConfigLoader.load()
        assert config.app_name == "base"
        assert config.workers == 1


class TestEnvInterpolation:
    """Tests for `${NAME}` (and `${NAME:-default}`) placeholder expansion."""

    def test_expands_whole_value(self, monkeypatch):
        from secbaas.community.config import ConfigLoader

        monkeypatch.setenv("SECRET", "s3-cr-3t")
        out = ConfigLoader._expand_env_placeholders({"key": "${SECRET}"})
        assert out == {"key": "s3-cr-3t"}

    def test_expands_substring(self, monkeypatch):
        from secbaas.community.config import ConfigLoader

        monkeypatch.setenv("REGION", "cn-hangzhou")
        out = ConfigLoader._expand_env_placeholders(
            {"endpoint": "wss://${REGION}.example.com/ws"}
        )
        assert out == {"endpoint": "wss://cn-hangzhou.example.com/ws"}

    def test_recursive_into_dict_and_list(self, monkeypatch):
        from secbaas.community.config import ConfigLoader

        monkeypatch.setenv("V", "ok")
        data = {
            "a": "${V}",
            "nested": {"b": "pre-${V}-post"},
            "list": ["${V}", "literal", {"deep": "${V}"}],
        }
        assert ConfigLoader._expand_env_placeholders(data) == {
            "a": "ok",
            "nested": {"b": "pre-ok-post"},
            "list": ["ok", "literal", {"deep": "ok"}],
        }

    def test_non_string_values_left_untouched(self):
        from secbaas.community.config import ConfigLoader

        data = {"port": 8888, "flag": True, "items": [1, 2], "none": None}
        assert ConfigLoader._expand_env_placeholders(data) == data

    def test_default_when_unset(self, monkeypatch):
        from secbaas.community.config import ConfigLoader

        monkeypatch.delenv("MISSING_VAR", raising=False)
        out = ConfigLoader._expand_env_placeholders({"a": "${MISSING_VAR:-fallback}"})
        assert out == {"a": "fallback"}

    def test_empty_default_when_unset(self, monkeypatch):
        from secbaas.community.config import ConfigLoader

        monkeypatch.delenv("MISSING_VAR", raising=False)
        out = ConfigLoader._expand_env_placeholders({"a": "${MISSING_VAR:-}"})
        assert out == {"a": ""}

    def test_empty_string_env_value_is_used(self, monkeypatch):
        from secbaas.community.config import ConfigLoader

        # An explicitly-set empty env var is a real value, not "unset".
        monkeypatch.setenv("EMPTY_SET", "")
        out = ConfigLoader._expand_env_placeholders({"a": "${EMPTY_SET}"})
        assert out == {"a": ""}

    def test_missing_without_default_raises(self, monkeypatch):
        from secbaas.community.config import ConfigLoader

        # BaaS defaults to strict: an unresolvable, un-defaulted placeholder
        # raises rather than passing through.
        monkeypatch.delenv("NOPE_MISSING", raising=False)
        with pytest.raises(KeyError, match="NOPE_MISSING"):
            ConfigLoader._expand_env_placeholders({"a": "${NOPE_MISSING}"})

    def test_backward_compatible_mode_left_unchanged(self, monkeypatch):
        from secbaas.community.config import ConfigLoader

        monkeypatch.delenv("NOPE_MISSING", raising=False)
        out = ConfigLoader._expand_env_placeholders(
            {"a": "${NOPE_MISSING}"}, strict=False
        )
        assert out == {"a": "${NOPE_MISSING}"}

    def test_does_not_mutate_input(self, monkeypatch):
        from secbaas.community.config import ConfigLoader

        monkeypatch.setenv("V", "ok")
        data = {"nested": {"a": "${V}"}}
        ConfigLoader._expand_env_placeholders(data)
        assert data == {"nested": {"a": "${V}"}}

    def test_load_expands_placeholders_and_coerces_type(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "user_config:\n  secret: ${MY_SECRET}\n"
            "module_config:\n  web:\n    port: ${WEB_PORT}\n"
        )
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.setenv("MY_SECRET", "topsecret")
        monkeypatch.setenv("WEB_PORT", "9999")
        from secbaas.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.user_config["secret"] == "topsecret"
        assert config.module_config.web.port == 9999  # pydantic coerced str->int


class TestGetConfig:
    """End-to-end: get_config() strict passthrough and BaaS defaults."""

    def test_get_config_strict_default_raises(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "user_config:\n  secret: ${REQUIRED_VAR}\n"
        )
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("REQUIRED_VAR", raising=False)
        from secbaas.community.config import get_config, reset_config

        reset_config()
        with pytest.raises(KeyError, match="REQUIRED_VAR"):
            get_config()

    def test_get_config_backward_compatible_mode(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "user_config:\n  secret: ${REQUIRED_VAR}\n"
        )
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("REQUIRED_VAR", raising=False)
        from secbaas.community.config import get_config

        config = get_config(strict=False)
        assert config.user_config["secret"] == "${REQUIRED_VAR}"


class TestAliyunOverlayStrictContract:
    """Strict ${VAR} contract of the shipped configs/application-aliyun.yaml.

    Exercises the REAL overlay file in the community config tree (not a
    hand-written tmp copy): full expansion under BAAS_DEPLOY_TENANT=aliyun,
    fail-closed KeyError when a referenced placeholder variable is missing,
    and unreachability (base "stub" values win) when the tenant is unset.
    """

    def _isolate_loader_env(self, monkeypatch):
        """Clear loader selectors and point at the real configs/ directory."""
        from pathlib import Path

        config_dir = Path(__file__).resolve().parents[3] / "configs"
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.delenv("COMMUNITY_DEPLOY", raising=False)
        monkeypatch.delenv("BAAS_DEPLOY_TENANT", raising=False)
        for name in (
            "FT_OSS_ENDPOINT",
            "FT_OSS_EXTERNAL_ENDPOINT",
            "FT_OSS_BUCKET",
            "FT_OSS_STAGING_ROOT",
            "FT_PROXY_BASE_URL",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))

    def test_aliyun_overlay_full_expansion(self, monkeypatch):
        """All 8 variables set: the real overlay merges and expands fully."""
        from secbaas.community.config import ConfigLoader

        self._isolate_loader_env(monkeypatch)
        monkeypatch.setenv("BAAS_DEPLOY_TENANT", "aliyun")
        monkeypatch.setenv("FT_OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
        monkeypatch.setenv(
            "FT_OSS_EXTERNAL_ENDPOINT", "https://oss-cn-hangzhou-ext.aliyuncs.com"
        )
        monkeypatch.setenv("FT_OSS_BUCKET", "my-bucket")
        monkeypatch.setenv("FT_OSS_STAGING_ROOT", "baas-file-transfer")
        monkeypatch.setenv("FT_PROXY_BASE_URL", "https://bff.example.com")

        config = ConfigLoader.load()
        plugins = config.user_config["plugins"]
        assert plugins["file_transfer"] == "real"
        assert plugins["session_file_url_projector"] == "aliyun_ack"
        assert config.user_config["env"]["deploy_tenant"] == "aliyun"
        oss = config.user_config["file_transfer_oss_aliyun"]
        assert oss["endpoint"] == "https://oss-cn-hangzhou.aliyuncs.com"
        assert oss["external_endpoint"] == "https://oss-cn-hangzhou-ext.aliyuncs.com"
        assert oss["bucket_name"] == "my-bucket"
        assert oss["staging_root_path"] == "baas-file-transfer"
        assert (
            config.user_config["session_file_url_proxy"]["proxy_base_url"]
            == "https://bff.example.com"
        )

    def test_aliyun_overlay_missing_var_raises_keyerror(self, monkeypatch):
        """A strict placeholder unset: load() fails closed with KeyError."""
        from secbaas.community.config import ConfigLoader

        self._isolate_loader_env(monkeypatch)
        monkeypatch.setenv("BAAS_DEPLOY_TENANT", "aliyun")
        monkeypatch.setenv("FT_OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
        monkeypatch.setenv(
            "FT_OSS_EXTERNAL_ENDPOINT", "https://oss-cn-hangzhou-ext.aliyuncs.com"
        )
        # FT_OSS_BUCKET deliberately left unset: the placeholder has no
        # ":-default" fallback, so startup fails before any service runs.
        monkeypatch.setenv("FT_OSS_STAGING_ROOT", "baas-file-transfer")
        monkeypatch.setenv("FT_PROXY_BASE_URL", "https://bff.example.com")

        with pytest.raises(KeyError, match="FT_OSS_BUCKET"):
            ConfigLoader.load()

    def test_aliyun_overlay_unreachable_without_tenant(self, monkeypatch):
        """Tenant unset: the overlay file is unreachable — base stub wins."""
        from secbaas.community.config import ConfigLoader

        self._isolate_loader_env(monkeypatch)
        # The reference variables are set but must not matter: without
        # BAAS_DEPLOY_TENANT the overlay is never loaded, so it cannot leak
        # aliyun values into the main-site config.
        monkeypatch.setenv("FT_OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
        monkeypatch.setenv(
            "FT_OSS_EXTERNAL_ENDPOINT", "https://oss-cn-hangzhou-ext.aliyuncs.com"
        )
        monkeypatch.setenv("FT_OSS_BUCKET", "my-bucket")
        monkeypatch.setenv("FT_OSS_STAGING_ROOT", "baas-file-transfer")
        monkeypatch.setenv("FT_PROXY_BASE_URL", "https://bff.example.com")

        config = ConfigLoader.load()
        plugins = config.user_config["plugins"]
        assert plugins["file_transfer"] == "stub"
        assert plugins["session_file_url_projector"] == "stub"
        assert config.user_config["file_transfer_oss_aliyun"]["endpoint"] == ""
        assert config.user_config["session_file_url_proxy"]["proxy_base_url"] == ""
