"""Community-safe YAML configuration provider (B2).

Reads ``configs/application.yaml`` plus the overlay owned by a semantic deploy
profile, deep-merges them, expands ``${ENV_VAR}`` placeholders, and returns an
:class:`~agentclaw.community.core.config.provider.AppConfig`. This is the default
provider (community / test / local) and the body of the loader the local-mode
monkeypatch used to carry inline.

Imports nothing internal beyond the neutral :class:`AppConfig` type — no
``sofapy_base``, no plugins — so a community checkout can read its configuration
with no company packages installed.
"""
from __future__ import annotations

import logging
import os
import re
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

# Placeholder syntax: ${NAME} or ${NAME:-default} (shell / k8s / envsubst style),
# matching the loaders in baas, gateway and proxy so one deployment spells its
# config the same way for every service. ``${NAME:-}`` yields an empty string.
_ENV_INTERP = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}"
)


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


def _expand_env_placeholders(data: Any, *, strict: bool = True) -> Any:
    """Recursively expand ``${NAME}`` placeholders in a merged config tree.

    Walks dict and list nodes; for string leaves, replaces every ``${NAME}`` (or
    ``${NAME:-default}``) occurrence with the value of environment variable
    ``NAME``. Non-string values are returned as-is.

    Resolution order for a placeholder:

    1. environment variable ``NAME`` if set (an empty string counts as set);
    2. the default given via ``:-default`` if present;
    3. when *strict* is True (the default, matching BaaS) it raises
       ``KeyError`` — a referenced env var that is neither set nor defaulted is a
       configuration error, and a deployment that forgot to wire a Secret should
       fail at boot rather than serve a half-configured surface. When *strict* is
       False the placeholder is left unchanged.

    Anything that must still boot bare therefore needs a default in the YAML
    (``${DATABASE_URL:-sqlite:///./data/agentclaw.db}``), not a naked
    ``${DATABASE_URL}``.

    This runs inside config loading — an approved site for raw environment access
    per AGENTS.md — and before the typed config providers read the tree, so they
    see resolved values and never reach for ``os.environ`` themselves.
    """

    def _env_replacer(match: re.Match[str]) -> str:
        name = match.group("name")
        if name in os.environ:
            return os.environ[name]
        default = match.group("default")
        if default is not None:
            return default
        if not strict:
            return match.group(0)
        raise KeyError(
            f"Environment variable {name!r} referenced by ${{{name}}} in config "
            "is not set and has no default"
        )

    if isinstance(data, dict):
        return {k: _expand_env_placeholders(v, strict=strict) for k, v in data.items()}
    if isinstance(data, list):
        return [_expand_env_placeholders(v, strict=strict) for v in data]
    if isinstance(data, str):
        return _ENV_INTERP.sub(_env_replacer, data)
    return data


def _load_yaml_configs(
    overlay_name: str = "application-dev.yaml",
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Merge application.yaml with the caller-selected overlay, expanding env vars.

    Three-layer merge: base → community overlay → corp overlay (optional).
    The corp overlay (``{overlay_name}-corp.yaml``) injects real credentials
    and values that should NOT live in community source (ocb-public). It is
    absent in community-only / test builds and simply skipped.
    """
    # B11: configs live in the community subtree (agentclaw/community/configs). In a
    # deploy the assembled runtime `configs/` (cwd) holds them; in the monorepo they
    # resolve from the subtree. Community never searches corp/configs — the test
    # suite reads only community overlays, and corp prod reads config via sofapy.
    config_dirs = [
        Path.cwd() / "configs",
        Path(__file__).resolve().parents[2] / "configs",  # agentclaw/community/configs
    ]

    # Corp overlay search dirs (local monorepo + deployed configs/).
    # Filename: application-singlebox-corp.yaml (derived from overlay name).
    stem = overlay_name.removesuffix(".yaml")
    corp_overlay_name = f"{stem}-corp.yaml"
    corp_config_dirs = [
        Path.cwd() / "configs",
        Path(__file__).resolve().parents[8] / "src" / "backend" / "src" / "agentclaw" / "corp" / "configs",
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
        merged = _deep_merge(base_config, overlay_config)

        # Third layer: corp overlay (optional, absent in community-only builds).
        for cdir in corp_config_dirs:
            corp_path = cdir / corp_overlay_name
            if corp_path.exists():
                with open(corp_path, "r", encoding="utf-8") as f:
                    corp_config = yaml.safe_load(f) or {}
                logger.info("YamlConfigProvider loaded corp overlay: %s", corp_path)
                merged = _deep_merge(merged, corp_config)
                break

        # Expand after the merge so an overlay can introduce a placeholder the
        # base does not carry, and before AppConfig so every consumer — typed
        # dataclass providers included — reads resolved values.
        return _expand_env_placeholders(merged, strict=strict)

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
