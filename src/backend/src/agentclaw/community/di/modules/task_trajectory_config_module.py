"""DI provider for ``TrajectoryAnalysisConfig`` (REQ-9, 决策 #10).

Lives OUT of ``di/modules/config_module.py`` (which sits at zero headroom under
the 1000-line architecture cap) so the P5a analyzer config provider does not push
that monolith over the threshold. Mirrors the ``DesktopSkillRecoveryConfigModule``
pattern: a small ``Module`` with one ``@singleton @provider`` that reads the
``task_trajectory`` block of ``user_config`` (via ``config_module.read_user_config``)
and the same ``_coerce`` helper ``ConfigModule`` uses, then returns a
``TrajectoryAnalysisConfig``. Registered in ``di/container.py`` alongside
``ConfigModule``.

YAML shape under ``user_config.task_trajectory``::

    task_trajectory:
      analysis_bot_id: "bot-traj-analyst"      # REQUIRED to enable do_analysis=true
      tc_bot_timeout_seconds: 180              # synchronous round-trip cap (float seconds)

The bot_id is deployment-configured, not per-request (决策 #10): the P5b service
reads ``analysis_bot_id`` and passes it as ``analysis_executor`` to
``TaskTrajectoryAnalyzer.analyze``; callers cannot choose the bot.
"""
from __future__ import annotations

from typing import Any

from injector import Module, provider, singleton

from agentclaw.community.di.modules import config_module
from agentclaw.community.di.task_trajectory_config import TrajectoryAnalysisConfig


def _block() -> dict[str, Any]:
    """Pull the ``task_trajectory`` block out of ``user_config`` (``{}`` if missing),
    mirroring the other config modules' ``_block`` helpers."""
    raw = config_module.read_user_config().get("task_trajectory") or {}
    return dict(raw) if isinstance(raw, dict) else {}


class TaskTrajectoryConfigModule(Module):
    """Bind ``TrajectoryAnalysisConfig`` to its deployment config (REQ-9, 决策 #10)."""

    @singleton
    @provider
    def task_trajectory_analysis(self) -> TrajectoryAnalysisConfig:
        block = _block()
        defaults = TrajectoryAnalysisConfig()
        return TrajectoryAnalysisConfig(
            analysis_bot_id=(
                config_module._coerce(
                    block,
                    "analysis_bot_id",
                    str,
                    defaults.analysis_bot_id,
                    "task_trajectory",
                )
                or defaults.analysis_bot_id
            ),
            tc_bot_timeout_seconds=config_module._coerce(
                block,
                "tc_bot_timeout_seconds",
                float,
                defaults.tc_bot_timeout_seconds,
                "task_trajectory",
            ),
        )


__all__ = ["TaskTrajectoryConfigModule"]
