"""
YAML + Environment Variable Configuration Provider

Combines YAML configuration with environment variable overrides.
"""
import os
from typing import Any, Optional
import yaml


class YamlEnvConfigProvider:
    """
    Configuration provider that loads from YAML file with env var overrides.

    Priority: Environment variables > YAML config > defaults

    This is the OSS-friendly config provider that replaces internal DRM-based config.
    """

    def __init__(self, config_path: Optional[str] = None):
        """Initialize config provider.

        Args:
            config_path: Path to YAML config file. If None, uses STARTUP_PROFILE env var.
        """
        self._config: dict = {}
        self._load_config(config_path)

    def _load_config(self, config_path: Optional[str]) -> None:
        """Load configuration from YAML file."""
        if config_path is None:
            profile = os.getenv("STARTUP_PROFILE", "opensource")
            config_path = f"configs/application-{profile}.yaml"

        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}

        # Inject env vars (highest priority)
        self._inject_env_overrides()

    def _inject_env_overrides(self) -> None:
        """Inject environment variable overrides into config."""
        # Common env var mappings
        env_mappings = {
            "LLM_BASE_URL": ["llm", "base_url"],
            "LLM_AUTH_TOKEN": ["llm", "auth_token"],
            "LLM_FAST_MODEL": ["llm", "fast_model"],
            "LLM_REASONING_MODEL": ["llm", "reasoning_model"],
            "LLM_ENABLED": ["llm", "enabled"],
            "EMBEDDING_BASE_URL": ["embedding", "base_url"],
            "EMBEDDING_AUTH_TOKEN": ["embedding", "auth_token"],
            "EMBEDDING_MODEL": ["embedding", "model"],
            "EMBEDDING_DIMENSION": ["embedding", "dimension"],
            "RERANKER_BASE_URL": ["reranker", "base_url"],
            "RERANKER_API_KEY": ["reranker", "api_key"],
            "RERANKER_MODEL": ["reranker", "model"],
            "VECTOR_BACKEND": ["vector", "backend"],
            "QDRANT_LOCAL_PATH": ["vector", "qdrant", "local_path"],
            "WORKER_REGISTRY_DATABASE_MODE": ["database", "mode"],
            "WORKER_REGISTRY_SQLITE_DB_PATH": ["database", "sqlite", "path"],
        }

        for env_key, config_path in env_mappings.items():
            value = os.getenv(env_key)
            if value is not None:
                self._set_nested(config_path, value)

    def _set_nested(self, path: list[str], value: Any) -> None:
        """Set a nested config value using path list."""
        current = self._config
        for key in path[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[path[-1]] = value

    def _get_nested(self, path: list[str], default: Any = None) -> Any:
        """Get a nested config value using path list."""
        current = self._config
        for key in path:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value by key (supports dot notation)."""
        # First check environment variable directly
        env_value = os.getenv(key)
        if env_value is not None:
            return env_value

        # Then check nested config
        path = key.split(".")
        return self._get_nested(path, default)

    def get_int(self, key: str, default: int = 0) -> int:
        """Get config value as integer."""
        value = self.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Get config value as float."""
        value = self.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get config value as boolean."""
        value = self.get(key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)

    def get_list(self, key: str, default: Optional[list] = None) -> list:
        """Get config value as list."""
        value = self.get(key)
        if value is None:
            return default if default is not None else []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [item.strip() for item in value.split(",")]
        return [value]

    def get_dict(self, key: str, default: Optional[dict] = None) -> dict:
        """Get config value as dict."""
        value = self.get(key)
        if value is None:
            return default if default is not None else {}
        if isinstance(value, dict):
            return value
        return default if default is not None else {}

    def reload(self) -> None:
        """Reload configuration from source."""
        self._config = {}
        self._load_config(None)