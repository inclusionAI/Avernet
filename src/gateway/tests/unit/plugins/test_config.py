"""Unit tests for configuration loading, merging, parsing, and path utilities."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gateway.community.config import (
    Config,
    ConfigLoader,
    LogConfig,
    ModuleConfig,
    WebConfig,
    get_config,
    get_config_by_path,
    reset_config,
)
from gateway.community.config._config_loader import (
    _load_yaml,
    _merge,
    _parse_config,
    _resolve_base_path,
    _resolve_overlay_path,
)

# ── _merge ──────────────────────────────────────────────────────────────────


class TestMerge:
    def test_overlay_overrides_base(self) -> None:
        result = _merge({"a": 1, "b": 2}, {"b": 3})
        assert result == {"a": 1, "b": 3}

    def test_nested_dict_recursive_merge(self) -> None:
        base = {"outer": {"inner": 1, "keep": True}}
        overlay = {"outer": {"inner": 2}}
        result = _merge(base, overlay)
        assert result == {"outer": {"inner": 2, "keep": True}}

    def test_empty_overlay_returns_base_copy(self) -> None:
        base = {"a": 1}
        result = _merge(base, {})
        assert result == {"a": 1}
        # Ensure a copy is returned, not the same object.
        assert result is not base

    def test_empty_base_with_overlay(self) -> None:
        result = _merge({}, {"x": "y"})
        assert result == {"x": "y"}

    def test_overlay_replaces_non_dict_with_dict(self) -> None:
        result = _merge({"a": 1}, {"a": {"b": 2}})
        assert result == {"a": {"b": 2}}

    def test_overlay_replaces_dict_with_non_dict(self) -> None:
        result = _merge({"a": {"b": 1}}, {"a": 42})
        assert result == {"a": 42}

    def test_deeply_nested_merge(self) -> None:
        base = {"l1": {"l2": {"l3": {"a": 1, "b": 2}}}}
        overlay = {"l1": {"l2": {"l3": {"b": 3, "c": 4}}}}
        result = _merge(base, overlay)
        assert result == {"l1": {"l2": {"l3": {"a": 1, "b": 3, "c": 4}}}}

    def test_none_overlay_treated_as_empty(self) -> None:
        result = _merge({"a": 1}, None)  # type: ignore[arg-type]
        assert result == {"a": 1}


# ── _load_yaml ──────────────────────────────────────────────────────────────


class TestLoadYaml:
    def test_none_path_returns_empty(self) -> None:
        assert _load_yaml(None) == {}

    def test_nonexistent_path_returns_empty(self, tmp_path: Path) -> None:
        assert _load_yaml(tmp_path / "nope.yaml") == {}

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text("")
        assert _load_yaml(p) == {}

    def test_valid_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "app.yaml"
        p.write_text("app_name: test\ntimeout: 30\n")
        assert _load_yaml(p) == {"app_name": "test", "timeout": 30}

    def test_non_dict_content_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "list.yaml"
        p.write_text("- a\n- b\n")
        assert _load_yaml(p) == {}

    def test_null_content_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "null.yaml"
        p.write_text("null\n")
        assert _load_yaml(p) == {}


# ── _parse_config ────────────────────────────────────────────────────────────


class TestParseConfig:
    def test_empty_dict_returns_defaults(self) -> None:
        config = _parse_config({})
        assert config.app_name == "gateway"
        assert config.enable_sidecar is False
        assert config.workers == 1
        assert config.log_config.log_level == "INFO"
        assert config.log_config.log_dir == ""
        assert config.module_config.web is None
        assert config.raw == {}

    def test_full_config(self) -> None:
        raw = {
            "app_name": "my-gateway",
            "enable_sidecar": True,
            "workers": 4,
            "log_config": {
                "log_level": "DEBUG",
                "log_dir": "/var/log/gw",
                "trace_log_dir": "/var/log/gw/trace",
            },
            "module_config": {
                "web": {
                    "port": 9999,
                    "start": "custom.module:app",
                },
            },
        }
        config = _parse_config(raw)
        assert config.app_name == "my-gateway"
        assert config.enable_sidecar is True
        assert config.workers == 4
        assert config.log_config.log_level == "DEBUG"
        assert config.log_config.log_dir == "/var/log/gw"
        assert config.log_config.trace_log_dir == "/var/log/gw/trace"
        assert config.module_config.web is not None
        assert config.module_config.web.port == 9999
        assert config.module_config.web.start == "custom.module:app"
        assert config.raw == raw

    def test_partial_web_config(self) -> None:
        raw = {"module_config": {"web": {"port": 7777}}}
        config = _parse_config(raw)
        assert config.module_config.web is not None
        assert config.module_config.web.port == 7777
        # start should use default
        assert config.module_config.web.start == WebConfig.start

    def test_workers_string_coerced_to_int(self) -> None:
        config = _parse_config({"workers": "3"})
        assert config.workers == 3
        assert isinstance(config.workers, int)

    def test_port_string_coerced_to_int(self) -> None:
        config = _parse_config({"module_config": {"web": {"port": "7000"}}})
        assert config.module_config.web is not None
        assert config.module_config.web.port == 7000


# ── _resolve_base_path / _resolve_overlay_path ───────────────────────────────


class TestResolvePaths:
    def test_base_path_no_env_no_cwd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GATEWAY_CONFIG_PATH", raising=False)
        monkeypatch.chdir("/")
        assert _resolve_base_path() is None

    def test_base_path_explicit_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        f = tmp_path / "my-config.yaml"
        f.touch()
        monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(f))
        assert _resolve_base_path() == f

    def test_base_path_explicit_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
        assert _resolve_base_path() == tmp_path / "application.yaml"

    def test_base_path_cwd_fallback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        configs = tmp_path / "configs"
        configs.mkdir()
        (configs / "application.yaml").touch()
        monkeypatch.delenv("GATEWAY_CONFIG_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        assert _resolve_base_path() == configs / "application.yaml"

    def test_overlay_path_explicit_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
        assert _resolve_overlay_path("dev") == tmp_path / "application-dev.yaml"

    def test_overlay_path_explicit_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        f = tmp_path / "application.yaml"
        f.touch()
        monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(f))
        assert _resolve_overlay_path("staging") == tmp_path / "application-staging.yaml"

    def test_overlay_path_no_env_no_cwd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GATEWAY_CONFIG_PATH", raising=False)
        monkeypatch.chdir("/")
        assert _resolve_overlay_path("prod") is None

    def test_overlay_path_cwd_fallback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        configs = tmp_path / "configs"
        configs.mkdir()
        (configs / "application-dev.yaml").touch()
        monkeypatch.delenv("GATEWAY_CONFIG_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        assert _resolve_overlay_path("dev") == configs / "application-dev.yaml"


# ── ConfigLoader.load / load_raw ─────────────────────────────────────────────


class TestConfigLoader:
    def test_load_with_no_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GATEWAY_CONFIG_PATH", raising=False)
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.chdir("/")
        config = ConfigLoader.load()
        assert config.app_name == "gateway"
        assert config.workers == 1

    def test_load_with_explicit_config_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        config_file = tmp_path / "application.yaml"
        config_file.write_text("app_name: test-app\nworkers: 8\n")
        monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(config_file))
        monkeypatch.delenv("SERVER_ENV", raising=False)
        config = ConfigLoader.load()
        assert config.app_name == "test-app"
        assert config.workers == 8

    def test_load_with_env_overlay(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        base = tmp_path / "application.yaml"
        base.write_text("app_name: base\nworkers: 1\n")
        overlay = tmp_path / "application-prod.yaml"
        overlay.write_text("workers: 4\n")
        monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(base))
        monkeypatch.setenv("SERVER_ENV", "prod")
        config = ConfigLoader.load()
        # base value preserved
        assert config.app_name == "base"
        # overlay value applied
        assert config.workers == 4

    def test_load_with_env_overlay_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        configs = tmp_path / "configs"
        configs.mkdir()
        (configs / "application.yaml").write_text("app_name: dir-base\n")
        (configs / "application-dev.yaml").write_text("app_name: dir-dev\n")
        monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(configs))
        monkeypatch.setenv("SERVER_ENV", "dev")
        config = ConfigLoader.load()
        assert config.app_name == "dir-dev"

    def test_load_raw_returns_raw_dict(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        config_file = tmp_path / "application.yaml"
        config_file.write_text("app_name: raw-test\nworkers: 2\n")
        monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(config_file))
        monkeypatch.delenv("SERVER_ENV", raising=False)
        raw = ConfigLoader.load_raw()
        assert raw["app_name"] == "raw-test"
        assert raw["workers"] == 2

    def test_load_env_overlay_missing_file_ignored(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        base = tmp_path / "application.yaml"
        base.write_text("app_name: base\n")
        monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(base))
        monkeypatch.setenv("SERVER_ENV", "nonexistent")
        config = ConfigLoader.load()
        assert config.app_name == "base"


# ── get_config / reset_config (singleton) ────────────────────────────────────


class TestConfigSingleton:
    def teardown_method(self) -> None:
        reset_config()

    def test_get_config_returns_same_instance(self) -> None:
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_reload_forces_new_instance(self) -> None:
        c1 = get_config()
        c2 = get_config(reload=True)
        assert c1 is not c2

    def test_reset_then_get_creates_new(self) -> None:
        c1 = get_config()
        reset_config()
        c2 = get_config()
        assert c1 is not c2


# ── get_config_by_path ───────────────────────────────────────────────────────


class TestGetConfigByPath:
    @pytest.fixture
    def config(self) -> Config:
        return Config(
            app_name="gw",
            workers=3,
            log_config=LogConfig(log_level="DEBUG", log_dir="/tmp"),
            module_config=ModuleConfig(web=WebConfig(port=9000)),
            raw={"custom": {"nested": {"deep": True}}},
        )

    def test_empty_path_returns_default(self, config: Config) -> None:
        assert get_config_by_path(config, "", default="fallback") == "fallback"

    def test_single_attribute(self, config: Config) -> None:
        assert get_config_by_path(config, "app_name") == "gw"
        assert get_config_by_path(config, "workers") == 3

    def test_nested_attribute(self, config: Config) -> None:
        assert get_config_by_path(config, "log_config.log_level") == "DEBUG"
        assert get_config_by_path(config, "module_config.web.port") == 9000

    def test_dict_key_lookup(self, config: Config) -> None:
        assert get_config_by_path(config, "raw.custom.nested.deep") is True

    def test_missing_attribute_returns_default(self, config: Config) -> None:
        assert get_config_by_path(config, "nonexistent", default=42) == 42

    def test_missing_nested_returns_default(self, config: Config) -> None:
        assert (
            get_config_by_path(config, "log_config.nonexistent", default=None) is None
        )

    def test_mixed_attr_and_dict(self, config: Config) -> None:
        assert get_config_by_path(config, "raw.custom.nested.deep") is True

    def test_default_none_when_not_specified(self, config: Config) -> None:
        assert get_config_by_path(config, "nope") is None
