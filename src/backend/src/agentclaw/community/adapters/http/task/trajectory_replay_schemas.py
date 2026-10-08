"""HTTP DTO translation for deterministic task-trajectory replay."""
from __future__ import annotations

from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.task.schemas import TrajectoryEventDTO
from agentclaw.community.core.task.task_context.task_trajectory.replay import (
    TaskTrajectoryReplay,
    TrajectoryReplayFrame,
)


class TrajectoryReplayFrameDTO(BaseModel):
    sequence: int = Field(..., description="事件在完整轨迹中的 0-based 序号")
    offset_ms: int = Field(..., description="按 playback_rate 缩放后的绝对播放偏移")
    delay_ms: int = Field(..., description="相对上一条源事件的播放等待时间")
    event: TrajectoryEventDTO


class TaskTrajectoryReplayDTO(BaseModel):
    task_id: str
    playback_rate: float
    source_started_at: int | None
    source_ended_at: int | None
    duration_ms: int
    from_sequence: int
    total_frames: int
    frames: list[TrajectoryReplayFrameDTO] = Field(default_factory=list)


def _enum_value(value: object) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _frame_to_dto(frame: TrajectoryReplayFrame) -> TrajectoryReplayFrameDTO:
    event = frame.event
    return TrajectoryReplayFrameDTO(
        sequence=frame.sequence,
        offset_ms=frame.offset_ms,
        delay_ms=frame.delay_ms,
        event=TrajectoryEventDTO(
            task_id=event.task_id,
            node_id=event.node_id,
            action_type=_enum_value(event.action_type) or "",
            action_result=event.action_result,
            attempt=event.attempt,
            gmt_create=event.gmt_create,
            gmt_modified=event.gmt_modified,
            action_input=event.action_input,
            status_from=_enum_value(event.status_from),
            status_to=_enum_value(event.status_to),
            error_type=_enum_value(event.error_type),
            error_msg=event.error_msg,
            boost_reason=event.boost_reason,
            holder_id=event.holder_id,
            analysis=event.analysis,
            output=event.output,
            session_msgs=event.session_msgs,
            artifacts=list(event.artifacts or []),
            search_probe=event.search_probe,
        ),
    )


def trajectory_replay_to_dto(replay: TaskTrajectoryReplay) -> TaskTrajectoryReplayDTO:
    """Translate the transport-agnostic replay projection to its HTTP contract."""
    return TaskTrajectoryReplayDTO(
        task_id=replay.task_id,
        playback_rate=replay.playback_rate,
        source_started_at=replay.source_started_at,
        source_ended_at=replay.source_ended_at,
        duration_ms=replay.duration_ms,
        from_sequence=replay.from_sequence,
        total_frames=replay.total_frames,
        frames=[_frame_to_dto(frame) for frame in replay.frames],
    )
