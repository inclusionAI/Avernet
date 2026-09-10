"""Strict configuration binding for Desktop Skill recovery."""

from __future__ import annotations

import math
from typing import Any

from injector import Module, provider, singleton

from agentclaw.community.di import config as cfg
from agentclaw.community.di.modules import config_module


def _block() -> dict[str, Any]:
    user_config = config_module.read_user_config()
    if "desktop_skill_recovery" not in user_config:
        return {}
    raw = user_config["desktop_skill_recovery"]
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("desktop_skill_recovery must be a mapping")
    return dict(raw)


def _as_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    raise TypeError(f"not a boolean: {raw!r}")


def _as_positive_int(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TypeError(f"not an integer: {raw!r}")
    return raw


def _as_finite_float(raw: Any) -> float:
    if isinstance(raw, bool):
        raise TypeError(f"not a number: {raw!r}")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"not a finite number: {raw!r}")
    return value


class DesktopSkillRecoveryConfigModule(Module):
    """Keep the recovery schema independent from the legacy config monolith."""

    @singleton
    @provider
    def desktop_skill_recovery(self) -> cfg.DesktopSkillRecoveryConfig:
        block = _block()
        defaults = cfg.DesktopSkillRecoveryConfig()
        allowed = {"enabled", "sweep_interval_seconds", "sweep_page_size"}
        unknown = sorted(str(key) for key in block if str(key) not in allowed)
        if unknown:
            raise ValueError(
                "unknown desktop_skill_recovery key(s) "
                + ", ".join(repr(key) for key in unknown)
            )
        return cfg.DesktopSkillRecoveryConfig(
            enabled=_as_bool(block.get("enabled", defaults.enabled)),
            sweep_interval_seconds=_as_finite_float(
                block.get("sweep_interval_seconds", defaults.sweep_interval_seconds)
            ),
            sweep_page_size=_as_positive_int(
                block.get("sweep_page_size", defaults.sweep_page_size)
            ),
        )


__all__ = ["DesktopSkillRecoveryConfigModule"]
