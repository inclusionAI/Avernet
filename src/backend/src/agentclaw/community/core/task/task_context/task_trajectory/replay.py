"""Deterministic, read-only playback projection for persisted task trajectories.

Replay means presenting already-recorded events with scaled timing metadata. It never
re-executes bots, tools, callbacks, or task state transitions. The caller supplies the
canonical timeline order assembled by the trajectory read service.

Authoritative spec: ``src/backend/specs/2026-10-08-task-trajectory-replay/spec.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agentclaw.community.core.task.task_context.task_trajectory.models import (
    TaskTrajectory,
    TrajectoryEvent,
)

_MIN_PLAYBACK_RATE = 0.1
_MAX_PLAYBACK_RATE = 10.0
_MAX_REPLAY_LIMIT = 1000


@dataclass(frozen=True)
class TrajectoryReplayFrame:
    """One replayable event plus its position on the scaled playback clock."""

    sequence: int
    offset_ms: int
    delay_ms: int
    event: TrajectoryEvent


@dataclass(frozen=True)
class TaskTrajectoryReplay:
    """A page of deterministic replay frames for one persisted trajectory."""

    task_id: str
    playback_rate: float
    source_started_at: int | None
    source_ended_at: int | None
    duration_ms: int
    from_sequence: int
    total_frames: int
    frames: list[TrajectoryReplayFrame] = field(default_factory=list)


def build_trajectory_replay(
    trajectory: TaskTrajectory,
    *,
    playback_rate: float = 1.0,
    from_sequence: int = 0,
    limit: int | None = None,
) -> TaskTrajectoryReplay:
    """Project a canonical trajectory into replay timing metadata.

    ``sequence`` always refers to the full source timeline. Pagination therefore does
    not renumber frames or reset offsets. ``delay_ms`` is measured from the preceding
    source event (zero for sequence 0), which lets clients request consecutive pages
    without introducing a timing discontinuity.
    """
    if not _MIN_PLAYBACK_RATE <= playback_rate <= _MAX_PLAYBACK_RATE:
        raise ValueError(
            f"playback_rate must be between {_MIN_PLAYBACK_RATE} and {_MAX_PLAYBACK_RATE}"
        )
    if from_sequence < 0:
        raise ValueError("from_sequence must be greater than or equal to 0")
    if limit is not None and not 1 <= limit <= _MAX_REPLAY_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_REPLAY_LIMIT}")

    timeline = trajectory.timeline
    total_frames = len(timeline)
    if not timeline:
        return TaskTrajectoryReplay(
            task_id=trajectory.task_id,
            playback_rate=playback_rate,
            source_started_at=None,
            source_ended_at=None,
            duration_ms=0,
            from_sequence=from_sequence,
            total_frames=0,
        )

    source_started_at = timeline[0].gmt_create
    source_ended_at = timeline[-1].gmt_create
    offsets = [
        round((event.gmt_create - source_started_at) / playback_rate)
        for event in timeline
    ]
    stop = total_frames if limit is None else min(total_frames, from_sequence + limit)
    frames = [
        TrajectoryReplayFrame(
            sequence=sequence,
            offset_ms=offsets[sequence],
            delay_ms=(0 if sequence == 0 else offsets[sequence] - offsets[sequence - 1]),
            event=timeline[sequence],
        )
        for sequence in range(min(from_sequence, total_frames), stop)
    ]
    return TaskTrajectoryReplay(
        task_id=trajectory.task_id,
        playback_rate=playback_rate,
        source_started_at=source_started_at,
        source_ended_at=source_ended_at,
        duration_ms=offsets[-1],
        from_sequence=from_sequence,
        total_frames=total_frames,
        frames=frames,
    )
