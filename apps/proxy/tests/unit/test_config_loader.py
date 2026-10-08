"""Unit tests for config loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from sandboxproxy.community.config import ConfigLoader


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestEnvPlaceholders:
    def test_env_expansion(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("TEST_PROXY_SECRET", "abc123")
        cfg = tmp_path / "application.yaml"
        _write(cfg, "user_config:\n  jwt:\n    secret: ${TEST_PROXY_SECRET}\n")
        monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(cfg))
        loaded = ConfigLoader.load()
        assert loaded.user_config.jwt.secret == "abc123"

    def test_default_fallback(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "application.yaml"
        _write(cfg, "app_name: ${MISSING_VAR:-sandboxproxy}\n")
        monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(cfg))
        loaded = ConfigLoader.load()
        assert loaded.app_name == "sandboxproxy"

    def test_default_with_nested_braces(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "application.yaml"
        _write(
            cfg,
            "user_config:\n"
            "  baas:\n"
            "    device_props_path: ${VAR:-/api/v1/devices/{provider_device_id}/props}\n",
        )
        monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(cfg))
        loaded = ConfigLoader.load()
        assert (
            loaded.user_config.baas["device_props_path"]
            == "/api/v1/devices/{provider_device_id}/props"
        )


class TestEnvOverlaySelection:
    """COMMUNITY_DEPLOY names the env overlay and wins over SERVER_ENV."""

    def test_community_deploy_wins_over_server_env(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cfg = tmp_path / "application.yaml"
        _write(cfg, "app_name: sandboxproxy\n")
        # Both overlays exist: COMMUNITY_DEPLOY must pick community, not prod.
        _write(tmp_path / "application-prod.yaml", "app_name: prod-app\n")
        _write(tmp_path / "application-community.yaml", "app_name: community-app\n")
        monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(cfg))
        monkeypatch.setenv("SERVER_ENV", "prod")
        monkeypatch.setenv("COMMUNITY_DEPLOY", "community")
        loaded = ConfigLoader.load()
        assert loaded.app_name == "community-app"

    def test_server_env_selects_overlay_when_community_deploy_unset(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cfg = tmp_path / "application.yaml"
        _write(cfg, "app_name: sandboxproxy\n")
        _write(tmp_path / "application-prod.yaml", "app_name: prod-app\n")
        monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(cfg))
        monkeypatch.setenv("SERVER_ENV", "prod")
        monkeypatch.delenv("COMMUNITY_DEPLOY", raising=False)
        loaded = ConfigLoader.load()
        assert loaded.app_name == "prod-app"

    def test_community_deploy_missing_overlay_ignored(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cfg = tmp_path / "application.yaml"
        _write(cfg, "app_name: sandboxproxy\n")
        monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(cfg))
        monkeypatch.setenv("SERVER_ENV", "prod")
        # No application-community.yaml: the env overlay is skipped, base wins.
        monkeypatch.setenv("COMMUNITY_DEPLOY", "community")
        loaded = ConfigLoader.load()
        assert loaded.app_name == "sandboxproxy"


class TestConfigLoad:
    def test_app_name(self) -> None:
        loaded = ConfigLoader.load()
        assert loaded.app_name == "sandboxproxy"

    def test_plugin_selection(self) -> None:
        loaded = ConfigLoader.load()
        assert loaded.user_config.plugins.resolver in {"prefix", "stub"}


class TestOverlayLoad:
    def test_named_overlay_overrides_base(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "application.yaml"
        _write(
            cfg,
            "app_name: sandboxproxy\n"
            "user_config:\n"
            "  plugins:\n"
            "    resolver: prefix\n"
            "    relay_client: baas\n",
        )
        overlay = tmp_path / "overlays" / "e2e-sqlite.yaml"
        _write(
            overlay,
            "user_config:\n  plugins:\n    resolver: stub\n    relay_client: stub\n",
        )
        monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(cfg))
        monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "e2e-sqlite")
        loaded = ConfigLoader.load()
        assert loaded.user_config.plugins.resolver == "stub"
        assert loaded.user_config.plugins.relay_client == "stub"

    def test_missing_overlay_raises(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "application.yaml"
        _write(cfg, "app_name: sandboxproxy\n")
        monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(cfg))
        monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "does-not-exist")
        with pytest.raises(FileNotFoundError):
            ConfigLoader.load()
