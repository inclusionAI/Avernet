"""Unit tests for deterministic, read-only task trajectory replay."""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.task_context.task_trajectory.models import (
    TaskTrajectory,
    TrajectoryActionType,
    TrajectoryEvent,
)
from agentclaw.community.core.task.task_context.task_trajectory.replay import (
    build_trajectory_replay,
)


def _trajectory() -> TaskTrajectory:
    timeline = [
        TrajectoryEvent("t1", "n1", TrajectoryActionType.SUBMIT, "success", 0, 1000, 1000),
        TrajectoryEvent("t1", "n1", TrajectoryActionType.PLAN, "success", 0, 1500, 1500),
        TrajectoryEvent("t1", "n2", TrajectoryActionType.EXECUTE, "success", 0, 1500, 1500),
        TrajectoryEvent("t1", "n2", TrajectoryActionType.VERIFY, "success", 0, 3000, 3000),
    ]
    return TaskTrajectory("t1", 3000, 3000, timeline=timeline)


@pytest.mark.unit
def test_replay_preserves_canonical_order_and_scales_timing():
    trajectory = _trajectory()

    replay = build_trajectory_replay(trajectory, playback_rate=2.0)

    assert [frame.sequence for frame in replay.frames] == [0, 1, 2, 3]
    assert [frame.offset_ms for frame in replay.frames] == [0, 250, 250, 1000]
    assert [frame.delay_ms for frame in replay.frames] == [0, 250, 0, 750]
    assert replay.duration_ms == 1000
    assert replay.source_started_at == 1000
    assert replay.source_ended_at == 3000
    assert replay.total_frames == 4
    assert [frame.event for frame in replay.frames] == trajectory.timeline


@pytest.mark.unit
def test_replay_page_keeps_global_sequences_offsets_and_inter_page_delay():
    replay = build_trajectory_replay(
        _trajectory(), playback_rate=1.0, from_sequence=3, limit=1
    )

    assert replay.from_sequence == 3
    assert replay.total_frames == 4
    assert len(replay.frames) == 1
    assert replay.frames[0].sequence == 3
    assert replay.frames[0].offset_ms == 2000
    assert replay.frames[0].delay_ms == 1500
    assert replay.duration_ms == 2000


@pytest.mark.unit
def test_replay_empty_trajectory_has_no_source_clock():
    replay = build_trajectory_replay(TaskTrajectory("empty", 1, 1))

    assert replay.frames == []
    assert replay.total_frames == 0
    assert replay.duration_ms == 0
    assert replay.source_started_at is None
    assert replay.source_ended_at is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"playback_rate": 0}, "playback_rate"),
        ({"playback_rate": 10.1}, "playback_rate"),
        ({"from_sequence": -1}, "from_sequence"),
        ({"limit": 0}, "limit"),
        ({"limit": 1001}, "limit"),
    ],
)
def test_replay_rejects_invalid_options(kwargs, message):
    with pytest.raises(ValueError, match=message):
        build_trajectory_replay(_trajectory(), **kwargs)
