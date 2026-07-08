"""Effective-config computation for the golden-snapshot behavior gate (OSS-0 #3).

This helper computes the **effective configuration** a deploy profile sees, so a
committed golden snapshot can prove that relocating corp values between config
files (base ``application.yaml`` → per-env overlays, and ``di/config.py`` code
defaults → overlays) changes **no profile's behavior**.

Two layers are captured per profile:

- ``raw_user_config`` — the deep-merged ``user_config`` dict (base ⊕ overlay),
  the same merge the loaders perform. This catches relocation of **every** yaml
  block, including corp-only blocks (``arca_sandbox`` …) that the neutral
  ``ConfigModule`` does not type.
- ``typed`` — every neutral ``ConfigModule`` dataclass, resolved against the
  merged config. This additionally catches ``di/config.py`` **code-default**
  changes (folded in by the providers), which the raw layer cannot see.

The merge uses ``_deep_merge`` (the ``YamlConfigProvider`` merge) consistently
for every profile. For the corp/sofapy path this is an approximation of sofapy's
own ``from_yaml`` merge, but since the same function is used to generate the
golden and to check it, any drift introduced by a relocation is detected
regardless — the invariant is *before == after*, not *matches sofapy exactly*.

``~`` expansion in workspace paths is normalized back to ``~`` so the snapshot is
machine-independent (CI / any dev home).
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

import yaml

from agentclaw.community.core.config.provider import (
    AppConfig,
    reset_config_provider,
    set_config_provider,
)
from agentclaw.community.core.config.yaml_provider import _deep_merge
from agentclaw.community.di.modules.config_module import ConfigModule

_BACKEND = Path(__file__).resolve().parents[3]
# B11 (3.2): the community effective-config harness reads ONLY community/configs, so
# it computes with corp absent. The corp rows (corp-prod / corp-default), whose
# overlays live under corp/configs, are covered by a corp-resident snapshot
# (tests/corp/config/) that reuses :func:`compute_effective_config` with corp search
# dirs. ``compute_effective_config`` takes ``config_dirs`` so both callers share the
# same merge/normalize logic; community defaults to its own configs and names no
# corp path.
COMMUNITY_CONFIGS = _BACKEND / "src" / "agentclaw" / "community" / "configs"
_COMMUNITY_CONFIG_DIRS = (COMMUNITY_CONFIGS,)

# The neutral ``ConfigModule`` providers, by method name. Corp-only config types
# (arca_sandbox, codefuse, antcode, …) live in ``CorpConfigModule`` and are not
# captured here — their yaml blocks are still covered by ``raw_user_config``.
_PROVIDER_METHODS = (
    "workspace",
    "whitelist",
    "cors",
    "secret_names",
    "llm_harness",
    "bot_chat",
    "bcn",
    "kb",
    "aix",
    "yuque",
    "bot_oss",
    "oss_to_nas",
    "device_provider",
    "device_allocation",
    "bcsfuse",
    "ecb",
    "baas",
    "workspace_hosting",
    "skill_scan",
    "masa_agent_eval",
    "desktop_bot_periodic_scan",
    "dormant_config",
    "dormant_notify",
    "task_queue_worker",
)

# The (base, overlay) pairs on a real behavior path. Community is added in
# Group B6 once ``application.yaml`` is neutral (before then, merging the
# still-corp base into community would poison the snapshot).
# The (base, overlay) pairs on a real behavior path — **community rows only** (B11
# 3.2). The corp rows (corp-prod / corp-default) live in
# ``tests/corp/config/effective_config.py``.
PROFILE_PAIRS: dict[str, tuple[str, str | None]] = {
    # DEPLOY_PROFILE=test loads the neutral community application-test.yaml.
    "test": ("application.yaml", "application-test.yaml"),
    "singlebox": ("application.yaml", "application-singlebox.yaml"),
    # Community loads the neutral base + community overlay (B6).
    "community": ("application.yaml", "application-community.yaml"),
}
# NOTE: ``application-sim.yaml`` is empty and ``sim`` is referenced nowhere in
# code/tests/scripts — a vestigial env. It is intentionally excluded (guarding it
# would force fabricating a full sim config).

_HOME = os.path.expanduser("~")


def _load(name: str | None, config_dirs: tuple[Path, ...]) -> dict[str, Any]:
    if not name:
        return {}
    for config_dir in config_dirs:
        path = config_dir / name
        if path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _normalize(obj: Any) -> Any:
    """JSON-safe, machine-independent normalization of a config value tree."""
    if dataclasses.is_dataclass(obj):
        return {k: _normalize(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(_normalize(x) for x in obj)
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalize(x) for x in obj]
    if isinstance(obj, str) and _HOME and _HOME in obj:
        return obj.replace(_HOME, "~")
    return obj


class _StaticProvider:
    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg

    def load(self) -> AppConfig:
        return self._cfg


def compute_effective_config(
    base: str,
    overlay: str | None,
    config_dirs: tuple[Path, ...] = _COMMUNITY_CONFIG_DIRS,
) -> dict[str, Any]:
    """Return ``{"raw_user_config": {...}, "typed": {...}}`` for a profile pair.

    ``config_dirs`` is the ordered search path for the base + overlay yaml files;
    it defaults to community/configs. The corp-resident snapshot passes
    ``(community/configs, corp/configs)`` so corp overlays resolve while the base
    still comes from the neutral community base — sharing this merge/normalize logic.

    Determinism: ``DORMANT_DRY_RUN`` (an ops env override read by the dormant
    provider) is neutralized for the duration so the snapshot depends only on the
    yaml + code defaults.
    """
    raw = _deep_merge(_load(base, config_dirs), _load(overlay, config_dirs))
    user_config = raw.get("user_config", {}) or {}
    app_cfg = AppConfig(
        user_config=user_config,
        raw=raw,
        app_name=raw.get("app_name", "agentclaw"),
        delegate=None,
    )

    saved_dormant = os.environ.pop("DORMANT_DRY_RUN", None)
    set_config_provider(_StaticProvider(app_cfg))
    try:
        module = ConfigModule()
        typed = {
            name: _normalize(getattr(module, name)()) for name in _PROVIDER_METHODS
        }
    finally:
        reset_config_provider()
        if saved_dormant is not None:
            os.environ["DORMANT_DRY_RUN"] = saved_dormant

    return {
        "raw_user_config": _normalize(user_config),
        "typed": typed,
    }
