"""Deployment config for the task trajectory analysis bot (REQ-9, 决策 #10).

Holds the ``TrajectoryAnalysisConfig`` dataclass OUT of ``di/config.py`` (which
sits at zero headroom under the 1000-line architecture cap) so the P5a analyzer
config addition does not push that shared config monolith over the threshold.
The matching DI provider lives in
``di/modules/task_trajectory_config_module.py`` (registered in ``di/container.py``
alongside ``ConfigModule``); the analyzer's DI binding in
``TaskPersistenceModule`` resolves ``TrajectoryAnalysisConfig`` via
``injector.get(...)`` (falling back to the analyzer's own ``_DefaultAnalysisConfig``
when this config module is absent — lightweight DI injectors stay green).

The ``analysis_bot_id`` is the **deployment-configured** bot the P5b trajectory
service calls on ``GET /trajectory?do_analysis=true``
(``analysis_type=tc_bot`` / ``analysis_executor=<bot_id>``); it is NOT a
per-request param — callers cannot choose the bot (决策 #10). Spec identifier
``task_trajectory_analysis_bot_id`` is realised here as the
``TrajectoryAnalysisConfig.analysis_bot_id`` field sourced from the
``task_trajectory.analysis_bot_id`` user_config key.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrajectoryAnalysisConfig:
    """Task trajectory analysis bot + timeout policy (REQ-9, 决策 #10).

    Sourced from the ``task_trajectory`` block of ``user_config`` by the
    ``TaskTrajectoryConfigModule`` provider. Defaults: ``analysis_bot_id`` None
    (no bot wired → the P5b service declines ``do_analysis=true``);
    ``tc_bot_timeout_seconds`` 180s (matches ``OpenApiBotPort.send_and_wait_async``'s
    own default so the analyzer is usable out-of-the-box).
    """

    analysis_bot_id: str | None = None
    tc_bot_timeout_seconds: float = 180.0


__all__ = ["TrajectoryAnalysisConfig"]
