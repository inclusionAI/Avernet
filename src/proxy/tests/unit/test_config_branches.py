"""Unit tests for config loader branches — overlays, env interpolation, port."""

from __future__ import annotations

from pathlib import Path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestOverlayMerge:
    def test_env_overlay_merges(self, tmp_path, monkeypatch) -> None:
        base = tmp_path / "application.yaml"
        _write(base, "app_name: base\nuser_config:\n  jwt:\n    secret: base-secret\n")
        overlay = tmp_path / "application-dev.yaml"
        _write(overlay, "app_name: dev\n")
        monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(base))
        monkeypatch.setenv("SERVER_ENV", "dev")

        from sandboxproxy.community.config import ConfigLoader

        loaded = ConfigLoader.load()
        assert loaded.app_name == "dev"
        assert loaded.user_config.jwt.secret == "base-secret"

    def test_port_from_env(self, tmp_path, monkeypatch) -> None:
        base = tmp_path / "application.yaml"
        _write(
            base,
            "app_name: sandboxproxy\nmodule_config:\n  web:\n    port: 8888\n",
        )
        monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(base))
        monkeypatch.setenv("SANDBOXPROXY_PORT", "9999")

        from sandboxproxy.community.config import ConfigLoader

        loaded = ConfigLoader.load()
        assert loaded.module_config.web.port == 9999

    def test_named_overlay_missing_raises(self, tmp_path, monkeypatch) -> None:
        base = tmp_path / "application.yaml"
        _write(base, "app_name: sandboxproxy\n")
        monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(base))
        monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "missing-overlay")

        import pytest

        from sandboxproxy.community.config import ConfigLoader

        with pytest.raises(FileNotFoundError):
            ConfigLoader.load()

    def test_directory_config_path(self, tmp_path, monkeypatch) -> None:
        d = tmp_path / "conf"
        _write(d / "application.yaml", "app_name: sandboxproxy\n")
        monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(d))

        from sandboxproxy.community.config import ConfigLoader

        loaded = ConfigLoader.load()
        assert loaded.app_name == "sandboxproxy"


class TestEnvInterpolationStrict:
    def test_strict_missing_env_raises(self, tmp_path, monkeypatch) -> None:
        base = tmp_path / "application.yaml"
        _write(base, "app_name: ${TOTALLY_ABSENT_VAR}\n")
        monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(base))

        import pytest

        from sandboxproxy.community.config import ConfigLoader

        with pytest.raises(KeyError):
            ConfigLoader.load(strict=True)
