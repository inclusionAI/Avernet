"""Configuration loader — loads and merges application config from files."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from gateway.community.logger import get_logger

from ._models import (
    Config,
    LogConfig,
    ModuleConfig,
    UserConfig,
    WebConfig,
)

logger = get_logger("config")


class ConfigLoader:
    """Load and merge gateway configuration."""

    # Placeholder syntax: ${NAME} or ${NAME:-default} (shell / k8s / envsubst
    # style). ${NAME:-} yields an empty string; a placeholder that references an
    # unset env var with no default raises KeyError (see _env_replacer).
    ENV_INTERP = re.compile(
        r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}"
    )

    @classmethod
    def _expand_env_placeholders(cls, data: Any, *, strict: bool = False) -> Any:
        """Recursively expand ``${NAME}`` env placeholders in a merged config tree.

        Walks dict and list nodes; for string leaves, expands every ``${NAME}``
        (or ``${NAME:-default}``) occurrence that names an environment variable.
        Non-string values are returned as-is.

        Resolution order for a placeholder:
        1. environment variable ``NAME`` if set (an empty string counts as set);
        2. the default given via ``:-default`` if present;
        3. when *strict* is False (default) the placeholder is left unchanged,
           preserving backward compatibility with intra-config references such
           as ``${backend_server_url}`` that a later config consumer (e.g. the
           forwarding ``DomainMap``) resolves; when *strict* is True it raises
           ``KeyError``, matching BaaS.
        """

        def _env_replacer(match: re.Match[str]) -> str:
            name = match.group("name")
            if name in os.environ:
                return os.environ[name]
            default = match.group("default")
            if default is not None:
                return default
            if strict:
                msg = (
                    f"Environment variable '{name}' referenced by "
                    f"${{{name}}} in config is not set and has no default"
                )
                raise KeyError(msg)
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

        # SERVER_ENV → configs/application-{env}.yaml (existing gateway mechanism).
        env = os.getenv("SERVER_ENV", "").strip()
        overlay_path = _resolve_overlay_path(env) if env else None
        if overlay_path and overlay_path.exists():
            base = _merge(base, _load_yaml(overlay_path))
            applied.append(str(overlay_path))

        # SOFAPY_CONFIG_OVERLAY → configs/overlays/{name}.yaml (baas-compatible).
        named_overlay = os.getenv("SOFAPY_CONFIG_OVERLAY", "").strip()
        if named_overlay:
            named_path = _resolve_named_overlay_path(named_overlay, base_path)
            if named_path is None or not named_path.exists():
                raise FileNotFoundError(
                    f"Overlay config not found: {named_path} "
                    f"(set via SOFAPY_CONFIG_OVERLAY={named_overlay})"
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

    @staticmethod
    def load_raw(*, strict: bool = False) -> dict:
        """Return the merged raw config dict (used by enterprise forwarding)."""
        return ConfigLoader.load(strict=strict).raw


def _load_yaml(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _explicit_config_path() -> str:
    """Return the explicit config path, preferring the gateway env var.

    ``GATEWAY_CONFIG_PATH`` is the public gateway contract. ``SOFAPY_CONFIG_PATH``
    is accepted as a compatibility alias for deployments that still set it.
    """
    return (
        os.getenv("GATEWAY_CONFIG_PATH", "").strip()
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
    # No config file found: fall back to built-in defaults (no file read).
    return None


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
    """Resolve ``configs/overlays/{name}.yaml`` for ``SOFAPY_CONFIG_OVERLAY``.

    Mirrors the baas overlay layout: an ``overlays/`` dir co-located with the
    base ``application.yaml`` (or ``configs/`` when resolving from CWD).
    """
    if base_path is not None:
        return base_path.parent / "overlays" / f"{name}.yaml"
    cwd_path = Path.cwd() / "configs" / "overlays" / f"{name}.yaml"
    if cwd_path.exists():
        return cwd_path
    return cwd_path


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
    port = int(web_raw.get("port", 8888))
    env_port = os.getenv("GATEWAY_PORT", "").strip()
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
    user_config = UserConfig.model_validate(
        user_raw if isinstance(user_raw, dict) else {}
    )
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
