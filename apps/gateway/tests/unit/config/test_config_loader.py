"""Tests for gateway ConfigLoader — env-backed placeholder expansion.

Mirrors the BaaS loader behaviour: after base/env/named-overlay merge, the
merged config tree is run through ``_expand_env_placeholders()`` before being
parsed into the typed ``Config``. Expansion is configurable: the default
(backward-compatible) mode leaves unresolvable, un-defaulted placeholders
intact for their consumer; strict mode raises ``KeyError`` (BaaS-aligned).
"""

import pytest


class TestEnvInterpolation:
    """Tests for `${NAME}` (and `${NAME:-default}`) placeholder expansion."""

    def test_expands_whole_value(self, monkeypatch):
        from gateway.community.config import ConfigLoader

        monkeypatch.setenv("SECRET", "s3-cr-3t")
        out = ConfigLoader._expand_env_placeholders({"key": "${SECRET}"})
        assert out == {"key": "s3-cr-3t"}

    def test_expands_substring(self, monkeypatch):
        from gateway.community.config import ConfigLoader

        monkeypatch.setenv("REGION", "cn-hangzhou")
        out = ConfigLoader._expand_env_placeholders(
            {"endpoint": "wss://${REGION}.example.com/ws"}
        )
        assert out == {"endpoint": "wss://cn-hangzhou.example.com/ws"}

    def test_recursive_into_dict_and_list(self, monkeypatch):
        from gateway.community.config import ConfigLoader

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
        from gateway.community.config import ConfigLoader

        data = {"port": 8888, "flag": True, "items": [1, 2], "none": None}
        assert ConfigLoader._expand_env_placeholders(data) == data

    def test_default_when_unset(self, monkeypatch):
        from gateway.community.config import ConfigLoader

        monkeypatch.delenv("MISSING_VAR", raising=False)
        out = ConfigLoader._expand_env_placeholders({"a": "${MISSING_VAR:-fallback}"})
        assert out == {"a": "fallback"}

    def test_empty_default_when_unset(self, monkeypatch):
        from gateway.community.config import ConfigLoader

        monkeypatch.delenv("MISSING_VAR", raising=False)
        out = ConfigLoader._expand_env_placeholders({"a": "${MISSING_VAR:-}"})
        assert out == {"a": ""}

    def test_empty_string_env_value_is_used(self, monkeypatch):
        from gateway.community.config import ConfigLoader

        # An explicitly-set empty env var is a real value, not "unset".
        monkeypatch.setenv("EMPTY_SET", "")
        out = ConfigLoader._expand_env_placeholders({"a": "${EMPTY_SET}"})
        assert out == {"a": ""}

    def test_missing_without_default_left_unchanged(self, monkeypatch):
        from gateway.community.config import ConfigLoader

        # A placeholder that is neither an env var nor given a default is left
        # intact so a later config consumer (e.g. the forwarding DomainMap) can
        # resolve it — backward compatible with intra-config references.
        monkeypatch.delenv("NOPE_MISSING", raising=False)
        out = ConfigLoader._expand_env_placeholders({"a": "${NOPE_MISSING}"})
        assert out == {"a": "${NOPE_MISSING}"}

    def test_strict_mode_raises_on_unresolvable(self, monkeypatch):
        from gateway.community.config import ConfigLoader

        monkeypatch.delenv("NOPE_MISSING", raising=False)
        with pytest.raises(KeyError, match="NOPE_MISSING"):
            ConfigLoader._expand_env_placeholders({"a": "${NOPE_MISSING}"}, strict=True)

    def test_does_not_mutate_input(self, monkeypatch):
        from gateway.community.config import ConfigLoader

        monkeypatch.setenv("V", "ok")
        data = {"nested": {"a": "${V}"}}
        ConfigLoader._expand_env_placeholders(data)
        assert data == {"nested": {"a": "${V}"}}


class TestLoadEnvInterpolation:
    """End-to-end: load() expands placeholders into typed fields and raw dict."""

    def test_load_expands_placeholders_and_coerces_type(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\n"
            "user_config:\n"
            "  plugins:\n"
            "    forwarder: ${FORWARDER}\n"
            "module_config:\n"
            "  web:\n"
            "    port: ${WEB_PORT}\n"
        )
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.setenv("SERVER_ENV", "")
        monkeypatch.setenv("FORWARDER", "httpx")
        monkeypatch.setenv("WEB_PORT", "9999")
        from gateway.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.user_config.plugins.forwarder == "httpx"
        assert config.module_config.web.port == 9999  # pydantic coerced str->int

    def test_load_raw_reflects_expanded_values(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "user_config:\n  plugins:\n    forwarder: ${FORWARDER}\n"
        )
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.setenv("FORWARDER", "httpx")
        from gateway.community.config import ConfigLoader

        raw = ConfigLoader.load_raw()
        assert raw["user_config"]["plugins"]["forwarder"] == "httpx"

    def test_config_without_placeholders_unchanged(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 1\nuser_config: {}\n"
        )
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        from gateway.community.config import ConfigLoader

        config = ConfigLoader.load()
        assert config.app_name == "base"
        assert config.workers == 1

    def test_missing_env_without_default_passes_through(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "user_config:\n  plugins:\n    forwarder: ${REQUIRED_VAR}\n"
        )
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("REQUIRED_VAR", raising=False)
        from gateway.community.config import ConfigLoader

        # Not an env var and no default → left intact for the consumer; load
        # must not fail.
        config = ConfigLoader.load()
        assert config.raw["user_config"]["plugins"]["forwarder"] == "${REQUIRED_VAR}"

    def test_strict_mode_raises_on_unresolvable_placeholder(
        self, monkeypatch, tmp_path
    ):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "user_config:\n  plugins:\n    forwarder: ${REQUIRED_VAR}\n"
        )
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("REQUIRED_VAR", raising=False)
        from gateway.community.config import ConfigLoader

        # Strict mode matches BaaS: unresolvable, un-defaulted placeholder
        # raises rather than passing through for a consumer.
        with pytest.raises(KeyError, match="REQUIRED_VAR"):
            ConfigLoader.load(strict=True)

    def test_strict_mode_still_expands_env(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "user_config:\n  plugins:\n    forwarder: ${FORWARDER}\n"
        )
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.setenv("FORWARDER", "httpx")
        from gateway.community.config import ConfigLoader

        config = ConfigLoader.load(strict=True)
        assert config.user_config.plugins.forwarder == "httpx"


class TestGetConfig:
    """End-to-end: get_config() strict passthrough and module defaults."""

    def test_get_config_strict_raises_on_unresolvable(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "user_config:\n  plugins:\n    forwarder: ${REQUIRED_VAR}\n"
        )
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("REQUIRED_VAR", raising=False)
        monkeypatch.delenv("GATEWAY_CONFIG_PATH", raising=False)
        from gateway.community.config import get_config, reset_config

        reset_config()
        with pytest.raises(KeyError, match="REQUIRED_VAR"):
            get_config(strict=True)

    def test_get_config_backward_compatible_by_default(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "user_config:\n  plugins:\n    forwarder: ${REQUIRED_VAR}\n"
        )
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        monkeypatch.delenv("REQUIRED_VAR", raising=False)
        monkeypatch.delenv("GATEWAY_CONFIG_PATH", raising=False)
        from gateway.community.config import get_config, reset_config

        reset_config()
        config = get_config()
        assert config.raw["user_config"]["plugins"]["forwarder"] == "${REQUIRED_VAR}"
