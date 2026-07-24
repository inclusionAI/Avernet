import os
from pathlib import Path

import yaml

from secbaas.logger import get_logger

from ._models import Config

logger = get_logger("config_loader")


class ConfigLoader:
    ENV_VAR = "SOFAPY_CONFIG_OVERLAY"
    CONFIG_PATH_ENV_VAR = "SOFAPY_CONFIG_PATH"
    DEFAULT_CONFIG_DIR = "configs"
    OVERLAY_DIR = "overlays"
    ENV_SERVER_ENV = "SERVER_ENV"

    @classmethod
    def _resolve_overlay_path(cls, name: str, config_dir: str) -> str:
        path = os.path.join(config_dir, cls.OVERLAY_DIR, f"{name}.yaml")
        logger.info("Resolved overlay config path: %s", path)
        return path

    @classmethod
    def _load_yaml_file(cls, path: str) -> dict:
        file_path = Path(path)
        if not file_path.exists():
            return {}
        with file_path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @classmethod
    def _load_base_from_yaml(cls, config_dir: str) -> dict:
        base_path = os.path.join(config_dir, "application.yaml")
        base = cls._load_yaml_file(base_path)
        server_env = os.getenv(cls.ENV_SERVER_ENV, "")
        if server_env:
            env_path = os.path.join(config_dir, f"application-{server_env}.yaml")
            env_data = cls._load_yaml_file(env_path)
            if env_data:
                base = Config.merge_configs(base, env_data)
        return base

    @classmethod
    def load(cls) -> Config:
        overlay = os.getenv(cls.ENV_VAR)
        config_dir = os.getenv(cls.CONFIG_PATH_ENV_VAR, cls.DEFAULT_CONFIG_DIR)
        base = cls._load_base_from_yaml(config_dir)
        if overlay:
            overlay_path = cls._resolve_overlay_path(overlay, config_dir)
            overlay_file = Path(overlay_path)
            if not overlay_file.exists():
                msg = (
                    f"Overlay config not found: {overlay_path} "
                    f"(set via {cls.ENV_VAR}={overlay})"
                )
                raise FileNotFoundError(msg)
            overlay_data = cls._load_yaml_file(overlay_path)
            base = Config.merge_configs(base, overlay_data)
        return Config(**base)
