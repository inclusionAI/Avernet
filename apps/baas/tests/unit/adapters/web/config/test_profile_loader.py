"""Tests for ConfigLoader — config loading with optional overlay."""

import pytest

from secbaas.community.config import ConfigLoader


class TestResolvePath:
    """Tests for ConfigLoader._resolve_overlay_path()."""

    def test_custom_profile_name(self):
        path = ConfigLoader._resolve_overlay_path("redis", "configs")
        assert path == "configs/overlays/redis.yaml"

    def test_custom_profile_with_different_config_dir(self):
        path = ConfigLoader._resolve_overlay_path("custom-feature", "/app/conf")
        assert path == "/app/conf/overlays/custom-feature.yaml"


class TestLoad:
    """Tests for ConfigLoader.load()."""

    def test_known_profile_loads_yaml_with_env_overlay(self, monkeypatch, tmp_path):
        """WHEN SOFAPY_CONFIG_OVERLAY is set, THEN loads base + overlay yaml."""
        (tmp_path / "application.yaml").write_text("app_name: base_app\nworkers: 4\n")
        overlay_dir = tmp_path / "overlays"
        overlay_dir.mkdir()
        (overlay_dir / "prod.yaml").write_text(
            "workers: 8\nuser_config:\n  mode: production\n"
        )
        monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "prod")
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(tmp_path))
        config = ConfigLoader.load()
        assert config.app_name == "base_app"
        assert config.workers == 8
        assert config.user_config["mode"] == "production"

    def test_known_profile_test_loads_yaml_with_env_overlay(
        self, monkeypatch, tmp_path
    ):
        """WHEN SOFAPY_CONFIG_OVERLAY=test, THEN loads base + test overlay."""
        (tmp_path / "application.yaml").write_text("app_name: base_app\nworkers: 2\n")
        overlay_dir = tmp_path / "overlays"
        overlay_dir.mkdir()
        (overlay_dir / "test.yaml").write_text("app_name: test_app\nworkers: 4\n")
        monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "test")
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(tmp_path))
        config = ConfigLoader.load()
        assert config.app_name == "test_app"
        assert config.workers == 4

    def test_custom_profile_loads_from_yaml(self, monkeypatch, tmp_path):
        """WHEN SOFAPY_CONFIG_OVERLAY is a custom name, THEN loads base yaml + overlay."""
        (tmp_path / "application.yaml").write_text("app_name: base_app\nworkers: 2\n")
        overlay_dir = tmp_path / "overlays"
        overlay_dir.mkdir()
        (overlay_dir / "custom.yaml").write_text(
            "app_name: custom_app\nuser_config:\n  key: val\n"
        )
        monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "custom")
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(tmp_path))

        config = ConfigLoader.load()
        assert config.app_name == "custom_app"
        assert config.user_config["key"] == "val"

    def test_not_set_falls_back_to_get_config(self, monkeypatch, tmp_path):
        """WHEN SOFAPY_CONFIG_OVERLAY is not set, THEN loads base yaml only."""
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        (tmp_path / "application.yaml").write_text(
            "app_name: default_app\nworkers: 1\n"
        )
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(tmp_path))
        config = ConfigLoader.load()
        assert config.app_name == "default_app"
        assert config.workers == 1

    def test_custom_profile_file_not_found_raises_error(self, monkeypatch, tmp_path):
        """WHEN custom overlay file does not exist, THEN raises FileNotFoundError."""
        (tmp_path / "application.yaml").write_text("app_name: base_app\nworkers: 2\n")
        monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "nonexistent")
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(tmp_path))

        with pytest.raises(FileNotFoundError):
            ConfigLoader.load()

    def test_custom_profile_merges_with_base_config(self, monkeypatch, tmp_path):
        """WHEN custom overlay file exists, THEN merges base + overlay."""
        (tmp_path / "application.yaml").write_text(
            "app_name: base\nworkers: 2\nuser_config:\n  base_key: base_value\n"
        )
        overlay_dir = tmp_path / "overlays"
        overlay_dir.mkdir()
        (overlay_dir / "override.yaml").write_text(
            "workers: 8\nuser_config:\n  extra_key: extra_value\n"
        )
        monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "override")
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(tmp_path))

        config = ConfigLoader.load()
        assert config.app_name == "base"
        assert config.workers == 8
        assert config.user_config["base_key"] == "base_value"
        assert config.user_config["extra_key"] == "extra_value"
