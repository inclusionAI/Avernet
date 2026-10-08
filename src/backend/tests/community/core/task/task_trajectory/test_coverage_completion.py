"""Focused branch pins that keep the trajectory package at 100% coverage."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.task_context.task_trajectory.analyzer import (
    TaskTrajectoryAnalyzer,
    _brief_event_artifacts,
    _build_ext_info_brief,
)
from agentclaw.community.core.task.task_context.task_trajectory.assembler import (
    _search_probe_from_ext_info,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    AnalysisType,
    TaskTrajectory,
    TrajectoryActionType,
    TrajectoryEvent,
)
from agentclaw.community.core.task.task_context.task_trajectory.time_utils import (
    epoch_ms_to_storage_datetime,
    storage_datetime_to_epoch_ms,
)
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_service import (
    TaskTrajectoryService,
    _build_ext_info_lookup,
)


def _event(
    action_type: TrajectoryActionType = TrajectoryActionType.DISPATCH,
    *,
    node_id: str = "n1",
    action_result: str = "success",
    at: int = 1_000,
    status_to: Status | None = None,
) -> TrajectoryEvent:
    return TrajectoryEvent(
        task_id="t1",
        node_id=node_id,
        action_type=action_type,
        action_result=action_result,
        attempt=0,
        gmt_create=at,
        gmt_modified=at,
        status_to=status_to,
    )


def _trajectory(events: list[TrajectoryEvent]) -> TaskTrajectory:
    return TaskTrajectory(
        task_id="t1",
        gmt_create=1_000,
        gmt_modified=1_000,
        timeline=events,
    )


class _Bot:
    def __init__(self) -> None:
        self.message: str | None = None

    async def send_and_wait_async(self, *, bot_id, message, metadata, timeout):
        self.message = message
        return {
            "result": {
                "content": json.dumps({"analysis_output": "ok"}),
            }
        }


def test_rule_unclassified_without_optional_error_message_and_miss_reason():
    terminal = _event(
        TrajectoryActionType.TRANSITION,
        action_result="cancelled",
        status_to=Status.CANCELLED,
    )
    result = asyncio.run(
        TaskTrajectoryAnalyzer().analyze(
            _trajectory([terminal]),
            lambda _event: None,
            analysis_type=AnalysisType.RULE,
            analysis_executor="rule_engine",
        )
    )
    assert result.failure_reason == "unclassified: transition cancelled"

    miss = _event(TrajectoryActionType.DISPATCH, action_result="miss")
    rationale = {
        "strategy_name": "search",
        "decision_mode": "skill",
        "candidates": [],
        "join_dropped": [],
        "miss_reason": "no_matching_candidates",
    }
    result = asyncio.run(
        TaskTrajectoryAnalyzer().analyze(
            _trajectory([miss]),
            lambda _event: {"_dispatch_rationale": rationale},
            analysis_type=AnalysisType.RULE,
            analysis_executor="rule_engine",
        )
    )
    assert "未选中原因=no_matching_candidates" in result.boost_reason

    rationale["miss_reason"] = ""
    result = asyncio.run(
        TaskTrajectoryAnalyzer().analyze(
            _trajectory([miss]),
            lambda _event: {"_dispatch_rationale": rationale},
            analysis_type=AnalysisType.RULE,
            analysis_executor="rule_engine",
        )
    )
    assert "未选中原因" not in result.boost_reason


def test_artifact_projection_covers_file_text_and_defensive_shapes():
    artifacts = _brief_event_artifacts(
        [
            "not-a-dict",
            {
                "kind": "report",
                "content": {
                    "kind": "file",
                    "media_type": "application/pdf",
                    "file_name": "report.pdf",
                    "resource_id": "res-1",
                    "size_bytes": 42,
                },
            },
            {
                "kind": "summary",
                "content": {
                    "kind": "text",
                    "media_type": "text/plain",
                    "text": "x" * 201,
                },
            },
            {"kind": "empty", "content": "unexpected"},
        ]
    )
    assert artifacts[0]["file_name"] == "report.pdf"
    assert artifacts[0]["resource_id"] == "res-1"
    assert artifacts[1]["text_preview"] == "x" * 200 + "…"
    assert artifacts[2]["text_preview"] == ""


@pytest.mark.asyncio
async def test_tc_bot_message_includes_projected_artifacts_and_filters_join_reasons():
    dispatch = _event(TrajectoryActionType.DISPATCH)
    dispatch.artifacts = [
        {
            "kind": "summary",
            "content": {
                "kind": "text",
                "media_type": "text/plain",
                "text": "done",
            },
        }
    ]
    rationale = {
        "strategy_name": "search",
        "decision_mode": "skill",
        "candidates": [],
        "join_dropped": ["bad", {"reason": None}, {"reason": "busy"}],
    }
    bot = _Bot()
    await TaskTrajectoryAnalyzer(bot=bot).analyze(
        _trajectory([dispatch]),
        lambda _event: {"_dispatch_rationale": rationale},
        analysis_type=AnalysisType.TC_BOT,
        analysis_executor="bot-analyst",
    )
    message = json.loads(bot.message)
    assert message["timeline"][0]["artifacts"][0]["text_preview"] == "done"
    assert message["ext_info_brief"]["last_dispatch_rationale"][
        "join_dropped_summary"
    ] == {"count": 3, "reasons": ["busy"]}


def test_ext_info_brief_handles_all_join_drop_item_shapes():
    event = _event()
    rationale = {
        "strategy_name": "search",
        "decision_mode": "skill",
        "candidates": [],
        "join_dropped": ["bad", {"reason": None}, {"reason": 7}],
    }
    brief = _build_ext_info_brief(
        [event], lambda _event: {"_dispatch_rationale": rationale}
    )
    assert brief["last_dispatch_rationale"]["join_dropped_summary"] == {
        "count": 3,
        "reasons": ["7"],
    }


def test_search_probe_rejects_non_objects_and_empty_relay_sampling():
    assert _search_probe_from_ext_info("[1, 2]", "search") is None
    assert _search_probe_from_ext_info('{"search_sampling": {}}', "search") is None
    assert (
        _search_probe_from_ext_info('{"search_sampling": ["bad-shape"]}', "search")
        is None
    )


def test_aware_storage_datetime_converts_directly():
    value = datetime(
        2026,
        1,
        2,
        3,
        4,
        5,
        678000,
        tzinfo=timezone(timedelta(hours=-5)),
    )
    assert storage_datetime_to_epoch_ms(value) == int(value.timestamp() * 1000)


class _Repo:
    def __init__(self, records) -> None:
        self.records = records

    def list_events_by_task(self, _task_id):
        return self.records


def test_ext_info_lookup_skips_non_object_json_and_continues():
    records = [
        SimpleNamespace(
            id=1,
            node_id="n1",
            action_type="dispatch",
            attempt=0,
            gmt_create=epoch_ms_to_storage_datetime(1_000),
            ext_info='["not", "an", "object"]',
        ),
        SimpleNamespace(
            id=2,
            node_id="n2",
            action_type="reset",
            attempt=0,
            gmt_create=epoch_ms_to_storage_datetime(2_000),
            ext_info='{"elapsed_ms": 7}',
        ),
    ]
    lookup = _build_ext_info_lookup(_Repo(records), "t1")
    assert lookup(_event(node_id="n1")) is None
    assert lookup(_event(TrajectoryActionType.RESET, node_id="n2", at=2_000)) == {
        "elapsed_ms": 7
    }


class _ArtifactService:
    def __init__(self, artifacts=None, error=None) -> None:
        self.artifacts = list(artifacts or [])
        self.error = error

    def list_artifacts_for_task(self, _task_id):
        if self.error:
            raise self.error
        return self.artifacts


class _Artifact:
    def __init__(self, node_id: str, artifact_id: str) -> None:
        self.scope = SimpleNamespace(node_id=node_id)
        self.artifact_id = artifact_id

    def to_dict(self):
        return {"artifact_id": self.artifact_id}


class _BrokenArtifact:
    @property
    def scope(self):
        raise RuntimeError("bad artifact")


def _service(artifact_service=None) -> TaskTrajectoryService:
    return TaskTrajectoryService(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        artifact_service=artifact_service,
    )


def test_artifact_enrichment_degrades_and_only_attaches_to_last_node_event():
    trajectory = _trajectory(
        [
            _event(node_id="n1", at=1_000),
            _event(node_id="n2", at=2_000),
            _event(node_id="n1", at=3_000),
            _event(node_id="n3", at=4_000),
        ]
    )
    _service(_ArtifactService(error=RuntimeError("store down")))._attach_node_artifacts(
        trajectory
    )
    assert all(event.artifacts is None for event in trajectory.timeline)

    _service(_ArtifactService([_BrokenArtifact()]))._attach_node_artifacts(trajectory)
    assert all(event.artifacts is None for event in trajectory.timeline)

    _service(
        _ArtifactService(
            [
                _BrokenArtifact(),
                _Artifact("n1", "a1"),
                _Artifact("n1", "a2"),
                _Artifact("missing-node", "orphan"),
            ]
        )
    )._attach_node_artifacts(trajectory)
    assert trajectory.timeline[0].artifacts is None
    assert trajectory.timeline[1].artifacts is None
    assert trajectory.timeline[2].artifacts == [
        {"artifact_id": "a1"},
        {"artifact_id": "a2"},
    ]
    assert trajectory.timeline[3].artifacts is None


def _submit_with_digest(digest: str) -> TaskTrajectory:
    event = _event(TrajectoryActionType.SUBMIT)
    event.action_input = digest
    return _trajectory([event])


def test_submit_goal_enrichment_handles_partial_and_empty_text():
    service = _service()
    acceptance_only = _submit_with_digest("digest-a")
    service._attach_submit_goal(
        acceptance_only,
        SimpleNamespace(
            goal=SimpleNamespace(
                objective="",
                acceptances=[SimpleNamespace(description="验收 A")],
            )
        ),
    )
    assert acceptance_only.timeline[0].action_input == "验收: 验收 A"

    objective_only = _submit_with_digest("digest-b")
    service._attach_submit_goal(
        objective_only,
        SimpleNamespace(
            goal=SimpleNamespace(objective="目标 B", acceptances=[]),
        ),
    )
    assert objective_only.timeline[0].action_input == "目标: 目标 B"

    empty = _submit_with_digest("digest-c")
    service._attach_submit_goal(
        empty,
        SimpleNamespace(goal=SimpleNamespace(objective="", acceptances=[])),
    )
    assert empty.timeline[0].action_input == "digest-c"
