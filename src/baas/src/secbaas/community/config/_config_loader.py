import os
import re
from pathlib import Path
from typing import Any

import yaml

from secbaas.community.logger import get_logger

from ._models import Config

logger = get_logger("config")


class ConfigLoader:
    ENV_VAR = "SOFAPY_CONFIG_OVERLAY"
    CONFIG_PATH_ENV_VAR = "SOFAPY_CONFIG_PATH"
    DEFAULT_CONFIG_DIR = "configs"
    OVERLAY_DIR = "overlays"
    ENV_SERVER_ENV = "SERVER_ENV"

    # Placeholder syntax: ${NAME} or ${NAME:-default} (shell / k8s / envsubst
    # style). ${NAME:-} yields an empty string; a placeholder that references an
    # unset env var with no default raises KeyError (see _env_replacer).
    ENV_INTERP = re.compile(
        r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}"
    )

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
    def _expand_env_placeholders(cls, data: Any) -> Any:
        """Recursively expand ``${NAME}`` placeholders in a merged config tree.

        Walks dict and list nodes; for string leaves, replaces every
        ``${NAME}`` (or ``${NAME:-default}``) occurrence with the value of the
        environment variable ``NAME``. Non-string values are returned as-is.

        Resolution order for a placeholder:
        1. environment variable ``NAME`` if set (an empty string counts as set);
        2. the default given via ``:-default`` if present;
        3. raise ``KeyError`` — a referenced env var that is neither set nor
           given a default is a configuration error, surfaced loudly rather than
           silently becoming an empty string.

        This runs inside config loading (an approved site for raw environment
        access per AGENTS.md). Replacing values here, before ``Config(**base)``,
        lets pydantic coerce env strings into field types (int/bool/...).
        """
        
        def _env_replacer(match: "re.Match[str]") -> str:
            name = match.group("name")
            if name in os.environ:
                return os.environ[name]
            default = match.group("default")
            if default is not None:
                return default
            msg = (
                f"Environment variable '{name}' referenced by "
                f"${{{name}}} in config is not set and has no default"
            )
            raise KeyError(msg)
        if isinstance(data, dict):
            return {k: cls._expand_env_placeholders(v) for k, v in data.items()}
        if isinstance(data, list):
            return [cls._expand_env_placeholders(v) for v in data]
        if isinstance(data, str):
            return cls.ENV_INTERP.sub(_env_replacer, data)
        return data

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
        base = cls._expand_env_placeholders(base)
        return Config(**base)