"""YAML configuration loader with environment + scenario overlay.

Mirrors the BAAS ``configs/`` convention:

- base ``application.yaml`` is deep-merged with an env overlay
  ``application-{env}.yaml`` (``DEPLOY_ENV`` / ``SERVER_ENV`` /
  ``ALIPAY_APP_ENV``).
- a scenario overlay from the dedicated ``overlays/<name>.yaml`` directory is
  merged on top via ``SOFAPY_CONFIG_OVERLAY`` (e.g. e2e-sqlite), so tests and
  alternate deployments layer extra settings without editing base files.
"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

__all__ = ["ApplicationConfig", "detect_env", "load_config"]


def detect_env() -> str:
    return (
        os.getenv("DEPLOY_ENV") or os.getenv("SERVER_ENV") or os.getenv("ALIPAY_APP_ENV") or ""
    ).lower()


def load_config(path: str | Path = "config/application.yaml") -> ApplicationConfig:
    base_path = Path(path)
    base = _read_yaml(base_path)
    env = detect_env()
    if env:
        overlay_path = base_path.with_name(f"{base_path.stem}-{env}{base_path.suffix}")
        if overlay_path.exists():
            base = _deep_merge(base, _read_yaml(overlay_path))
    scenario = os.getenv("SOFAPY_CONFIG_OVERLAY", "")
    if scenario:
        overlay_dir = base_path.parent / "overlays"
        scenario_path = overlay_dir / f"{scenario}.yaml"
        if scenario_path.exists():
            base = _deep_merge(base, _read_yaml(scenario_path))
    return ApplicationConfig(data=base, env=env)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


class ApplicationConfig:
    def __init__(self, data: dict[str, Any], env: str) -> None:
        self._data = data
        self.env = env

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> dict[str, Any]:
        value = self.get(name, {})
        return value if isinstance(value, dict) else {}
