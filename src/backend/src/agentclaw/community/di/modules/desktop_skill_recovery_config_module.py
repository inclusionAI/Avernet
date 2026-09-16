"""Strict configuration binding for Desktop Skill recovery."""

from __future__ import annotations

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


def _as_positive_int(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TypeError(f"not an integer: {raw!r}")
    return raw


class DesktopSkillRecoveryConfigModule(Module):
    """Keep the recovery schema independent from the legacy config monolith."""

    @singleton
    @provider
    def desktop_skill_recovery(self) -> cfg.DesktopSkillRecoveryConfig:
        block = _block()
        defaults = cfg.DesktopSkillRecoveryConfig()
        allowed = {"task_deadline_seconds"}
        unknown = sorted(str(key) for key in block if str(key) not in allowed)
        if unknown:
            raise ValueError(
                "unknown desktop_skill_recovery key(s) "
                + ", ".join(repr(key) for key in unknown)
            )
        return cfg.DesktopSkillRecoveryConfig(
            task_deadline_seconds=_as_positive_int(
                block.get("task_deadline_seconds", defaults.task_deadline_seconds)
            ),
        )


__all__ = ["DesktopSkillRecoveryConfigModule"]
