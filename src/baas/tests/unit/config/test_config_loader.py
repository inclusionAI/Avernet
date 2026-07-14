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

    def test_not_set_falls_back_to_base_yaml(self, tmp_path):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: default_app\nworkers: 1\nuser_config: {}\n"
        )
        import os as _os

        _os.environ.pop("SOFAPY_CONFIG_OVERLAY", None)
        _os.environ["SOFAPY_CONFIG_PATH"] = str(config_dir)
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
