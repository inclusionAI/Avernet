"""Configuration loader — loads and merges application config from files."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from ._models import (
    Config,
    LogConfig,
    ModuleConfig,
    PluginConfig,
    UserConfig,
    WebConfig,
)


class ConfigLoader:
    """Load and merge gateway configuration."""

    @staticmethod
    def load() -> Config:
        base_path = _resolve_base_path()
        base = _load_yaml(base_path) if base_path is not None else {}
        env = os.getenv("SERVER_ENV", "").strip()
        overlay_path = _resolve_overlay_path(env) if env else None
        if overlay_path and overlay_path.exists():
            overlay = _load_yaml(overlay_path)
            base = _merge(base, overlay)

        config_dir = base_path.parent if base_path is not None else None
        return _parse_config(base, config_dir=config_dir)

    @staticmethod
    def load_raw() -> dict:
        """Return the merged raw config dict (used by enterprise forwarding)."""
        return ConfigLoader.load().raw


def _load_yaml(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _resolve_base_path() -> Path | None:
    explicit = os.getenv("GATEWAY_CONFIG_PATH", "").strip()
    if explicit:
        p = Path(explicit)
        if p.is_dir():
            return p / "application.yaml"
        return p
    cwd_path = Path.cwd() / "configs" / "application.yaml"
    if cwd_path.exists():
        return cwd_path
    # No config file found: fall back to built-in defaults (no file read).
    return None


def _resolve_overlay_path(env: str) -> Path | None:
    explicit = os.getenv("GATEWAY_CONFIG_PATH", "").strip()
    if explicit:
        d = Path(explicit)
        if d.is_dir():
            return d / f"application-{env}.yaml"
        return d.parent / f"application-{env}.yaml"
    cwd_path = Path.cwd() / "configs" / f"application-{env}.yaml"
    if cwd_path.exists():
        return cwd_path
    return None


def _merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _parse_config(raw: dict, *, config_dir: Path | None = None) -> Config:
    module_raw = raw.get("module_config") or {}
    web_raw = module_raw.get("web") or {}
    web = WebConfig(
        port=int(web_raw.get("port", 8888)),
        start=web_raw.get("start", WebConfig.start),
        enable_api_docs=bool(web_raw.get("enable_api_docs", True)),
    )
    log_raw = raw.get("log_config") or {}
    log_config = LogConfig(
        trace_log_dir=log_raw.get("trace_log_dir", ""),
        log_level=log_raw.get("log_level", "INFO"),
        log_dir=log_raw.get("log_dir", ""),
    )
    user_raw = raw.get("user_config") or {}
    plugins_raw = user_raw.pop("plugins", {}) if isinstance(user_raw, dict) else {}
    user_raw = user_raw if isinstance(user_raw, dict) else {}
    plugin_config = PluginConfig.model_validate(plugins_raw)
    user_config = UserConfig(plugins=plugin_config, **user_raw)
    return Config(
        app_name=raw.get("app_name", "gateway"),
        enable_sidecar=bool(raw.get("enable_sidecar", False)),
        workers=int(raw.get("workers", 1)),
        log_config=log_config,
        module_config=ModuleConfig(web=web if web_raw else None),
        user_config=user_config,
        raw=raw,
        config_dir=config_dir,
    )
