"""Community-safe YAML configuration provider (B2).

Reads ``configs/application.yaml`` plus the overlay owned by a semantic deploy
profile and deep-merges them into an
:class:`~agentclaw.community.core.config.provider.AppConfig`. This is the default
provider (community / test / local) and the body of the loader the local-mode
monkeypatch used to carry inline.

Imports nothing internal beyond the neutral :class:`AppConfig` type — no
``sofapy_base``, no plugins — so a community checkout can read its configuration
with no company packages installed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

import yaml

from agentclaw.community.core.config.provider import AppConfig

logger = logging.getLogger(__name__)

_OVERLAY_BY_PROFILE = {
    "community": "application-community.yaml",
    "test": "application-test.yaml",
    "corp_test": "application-test.yaml",
    "singlebox": "application-singlebox.yaml",
}


class DeployProfileLike(Protocol):
    """Structural profile contract; avoids a Core-to-DI dependency."""

    @property
    def value(self) -> str: ...


def _profile_name(profile: DeployProfileLike) -> str:
    value = getattr(profile, "value", None)
    if not isinstance(value, str):
        raise ValueError(f"Unknown YAML config profile: {profile!r}")
    return value.strip().lower()


def _overlay_for_profile(profile: DeployProfileLike) -> str:
    normalized = _profile_name(profile)
    try:
        return _OVERLAY_BY_PROFILE[normalized]
    except KeyError as exc:
        raise ValueError(
            f"Unknown YAML config profile: {normalized!r}"
        ) from exc


def _load_yaml_configs(
    overlay_name: str = "application-dev.yaml",
) -> dict[str, Any]:
    """Merge application.yaml with the caller-selected overlay."""
    # B11: configs live in the community subtree (agentclaw/community/configs). In a
    # deploy the assembled runtime `configs/` (cwd) holds them; in the monorepo they
    # resolve from the subtree. Community never searches corp/configs — the test
    # suite reads only community overlays, and corp prod reads config via sofapy.
    config_dirs = [
        Path.cwd() / "configs",
        Path(__file__).resolve().parents[2] / "configs",  # agentclaw/community/configs
    ]

    for config_dir in config_dirs:
        base_path = config_dir / "application.yaml"
        overlay_path = config_dir / overlay_name
        if not (base_path.exists() and overlay_path.exists()):
            continue
        with open(base_path, "r", encoding="utf-8") as file:
            base_config = yaml.safe_load(file) or {}
        with open(overlay_path, "r", encoding="utf-8") as file:
            overlay_config = yaml.safe_load(file) or {}
        logger.info("YamlConfigProvider loaded overlay: %s", overlay_path)
        return _deep_merge(base_config, overlay_config)

    candidates = ", ".join(str(config_dir) for config_dir in config_dirs)
    raise FileNotFoundError(
        f"YamlConfigProvider requires {overlay_name!r} alongside application.yaml; "
        f"no complete config pair found in: {candidates}"
    )


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个 dict，override 覆盖 base。"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class YamlConfigProvider:
    """Load AppConfig from the overlay owned by a semantic deploy profile."""

    def __init__(self, profile: DeployProfileLike) -> None:
        self.profile = profile
        self.profile_name = _profile_name(profile)
        self.overlay_name = _overlay_for_profile(profile)

    def load(self) -> AppConfig:
        raw = _load_yaml_configs(self.overlay_name)
        return AppConfig(
            user_config=raw.get("user_config", {}),
            raw=raw,
            app_name=raw["app_name"],
            delegate=None,  # YAML path: no source object to forward to.
        )
