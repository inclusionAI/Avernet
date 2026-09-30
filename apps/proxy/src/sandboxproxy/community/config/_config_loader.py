"""Configuration loader — loads and merges application config from files."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from sandboxproxy.community.logger import get_logger

from ._models import (
    Config,
    LogConfig,
    ModuleConfig,
    UserConfig,
    WebConfig,
)

logger = get_logger("config")


class ConfigLoader:
    """Load and merge sandbox-proxy configuration."""

    ENV_INTERP = re.compile(
        r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>.*))?\}"
    )

    @classmethod
    def _expand_env_placeholders(cls, data: Any, *, strict: bool = False) -> Any:
        def _env_replacer(match: re.Match[str]) -> str:
            name = match.group("name")
            if name in os.environ:
                return os.environ[name]
            default = match.group("default")
            if default is not None:
                return default
            if strict:
                raise KeyError(
                    f"Environment variable '{name}' referenced by "
                    f"${{{name}}} is not set and has no default"
                )
            return match.group(0)

        if isinstance(data, dict):
            return {
                k: cls._expand_env_placeholders(v, strict=strict)
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [cls._expand_env_placeholders(v, strict=strict) for v in data]
        if isinstance(data, str):
            return cls.ENV_INTERP.sub(_env_replacer, data)
        return data

    @staticmethod
    def load(*, strict: bool = False) -> Config:
        base_path = _resolve_base_path()
        base = _load_yaml(base_path) if base_path is not None else {}
        applied: list[str] = []

        # COMMUNITY_DEPLOY, when set, wins over SERVER_ENV for the env overlay:
        # its value names application-<value>.yaml, so a community deployment
        # (COMMUNITY_DEPLOY=community) loads application-community.yaml.
        env = _resolve_env_overlay_name()
        overlay_path = _resolve_overlay_path(env) if env else None
        if overlay_path and overlay_path.exists():
            base = _merge(base, _load_yaml(overlay_path))
            applied.append(str(overlay_path))

        named_overlay = os.getenv("SOFAPY_CONFIG_OVERLAY", "").strip()
        if named_overlay:
            named_path = _resolve_named_overlay_path(named_overlay, base_path)
            if named_path is None or not named_path.exists():
                raise FileNotFoundError(
                    f"Overlay config not found: {named_path} "
                    f"(SOFAPY_CONFIG_OVERLAY={named_overlay})"
                )
            base = _merge(base, _load_yaml(named_path))
            applied.append(str(named_path))

        logger.info(
            "config loaded: base=%s env=%s overlays=%s",
            base_path,
            env or "(none)",
            applied or "(none)",
        )

        config_dir = base_path.parent if base_path is not None else None
        base = ConfigLoader._expand_env_placeholders(base, strict=strict)
        return _parse_config(base, config_dir=config_dir)


def _load_yaml(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data if isinstance(data, dict) else {}


def _explicit_config_path() -> str:
    return (
        os.getenv("SANDBOXPROXY_CONFIG_PATH", "").strip()
        or os.getenv("SOFAPY_CONFIG_PATH", "").strip()
    )


def _resolve_base_path() -> Path | None:
    explicit = _explicit_config_path()
    if explicit:
        p = Path(explicit)
        if p.is_dir():
            return p / "application.yaml"
        return p
    cwd_path = Path.cwd() / "configs" / "application.yaml"
    if cwd_path.exists():
        return cwd_path
    return None


def _resolve_env_overlay_name() -> str:
    """Return the suffix of the ``application-<suffix>.yaml`` env overlay.

    ``COMMUNITY_DEPLOY`` wins when set: its value names the overlay for a
    community deployment (e.g. ``COMMUNITY_DEPLOY=community`` loads
    ``application-community.yaml``) regardless of ``SERVER_ENV``. Falls back
    to the legacy ``SERVER_ENV`` behaviour when it is unset.
    """
    community_deploy = os.getenv("COMMUNITY_DEPLOY", "").strip()
    if community_deploy:
        return community_deploy
    return os.getenv("SERVER_ENV", "").strip()


def _resolve_overlay_path(env: str) -> Path | None:
    explicit = _explicit_config_path()
    if explicit:
        d = Path(explicit)
        if d.is_dir():
            return d / f"application-{env}.yaml"
        return d.parent / f"application-{env}.yaml"
    cwd_path = Path.cwd() / "configs" / f"application-{env}.yaml"
    if cwd_path.exists():
        return cwd_path
    return None


def _resolve_named_overlay_path(name: str, base_path: Path | None) -> Path | None:
    if base_path is not None:
        return base_path.parent / "overlays" / f"{name}.yaml"
    return Path.cwd() / "configs" / "overlays" / f"{name}.yaml"


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _parse_config(raw: dict[str, Any], *, config_dir: Path | None = None) -> Config:
    module_raw = raw.get("module_config") or {}
    web_raw = module_raw.get("web") or {}
    port = int(web_raw.get("port", 8888))
    env_port = os.getenv("SANDBOXPROXY_PORT", "").strip()
    if env_port:
        port = int(env_port)
    web = WebConfig(
        port=port,
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
    user_config = (
        UserConfig.model_validate(user_raw)
        if isinstance(user_raw, dict)
        else UserConfig()
    )
    return Config(
        app_name=raw.get("app_name", "sandboxproxy"),
        enable_sidecar=bool(raw.get("enable_sidecar", False)),
        workers=int(raw.get("workers", 1)),
        log_config=log_config,
        module_config=ModuleConfig(web=web if web_raw else None),
        user_config=user_config,
        raw=raw,
        config_dir=config_dir,
    )
