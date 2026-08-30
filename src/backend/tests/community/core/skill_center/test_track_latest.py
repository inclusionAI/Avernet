"""Track Latest task contracts after a Center Version becomes PUBLISHED."""

from __future__ import annotations

from datetime import UTC, datetime

from agentclaw.community.core.repository.track_latest_types import (
    TrackLatestCandidate,
    TrackLatestDependencyDelta,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    PublishedMaterializedSkillVersion,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.core.skill_center.services.track_latest import (
    BOT_TRACK_LATEST_RECONCILE_TASK,
    TRACK_LATEST_FANOUT_TASK,
    BotTrackLatestReconcileTaskHandler,
    TrackLatestFanoutTaskHandler,
    TrackLatestService,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset
from agentclaw.community.core.task_queue.types import Complete
from agentclaw.community.core.task_queue.types import Retry


class _Tasks:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def enqueue(self, task_type, payload, deadline_seconds, **kwargs):
        self.calls.append(
            {
                "task_type": task_type,
                "payload": payload,
                "deadline_seconds": deadline_seconds,
                **kwargs,
            }
        )
        return object()


def _published() -> PublishedMaterializedSkillVersion:
    return PublishedMaterializedSkillVersion(
        skill_version_id=101,
        skill_id=10,
        version_ordinal=2,
        status="PUBLISHED",
        skill_uuid="00000000-0000-4000-8000-000000000010",
        sc_version_number="2.0.0",
        sc_skill_id=9001,
        sc_version_id=10001,
        name="weather",
        description=None,
        metadata_json='{"mcp_dependencies":[{"code":"mcp.new"}]}',
        published_at=datetime(2026, 8, 30, tzinfo=UTC),
    )


def test_version_published_enqueues_one_skill_level_fanout() -> None:
    tasks = _Tasks()

    TrackLatestService(tasks).version_published(_published())

    assert tasks.calls == [
        {
            "task_type": TRACK_LATEST_FANOUT_TASK,
            "payload": {"skill_id": 10},
            "deadline_seconds": 30 * 60,
            "idempotency_key": "track-latest-fanout:10",
        }
    ]


class _Candidates:
    def list_candidates(self, *, env, skill_id):
        assert (env, skill_id) == ("pre", 10)
        return (
            TrackLatestCandidate(owner_id="owner-a", bot_id="bot-a"),
            TrackLatestCandidate(owner_id="owner-b", bot_id="bot-b"),
        )


def test_fanout_enqueues_one_level_triggered_task_per_candidate_bot() -> None:
    tasks = _Tasks()
    handler = TrackLatestFanoutTaskHandler(
        candidates=_Candidates(), tasks=tasks, env_provider=lambda: "pre"
    )

    outcome = handler.handle({"skill_id": 10})

    assert isinstance(outcome, Complete)
    assert [call["task_type"] for call in tasks.calls] == [
        BOT_TRACK_LATEST_RECONCILE_TASK,
        BOT_TRACK_LATEST_RECONCILE_TASK,
    ]
    assert [call["payload"] for call in tasks.calls] == [
        {"owner_id": "owner-a", "bot_id": "bot-a", "skill_id": 10},
        {"owner_id": "owner-b", "bot_id": "bot-b", "skill_id": 10},
    ]


class _Reader:
    def __init__(self, *, active: bool) -> None:
        self.active = active
        self.calls = 0

    def active_skill_assets(self, **_kwargs):
        self.calls += 1
        if not self.active:
            return ()
        return (
            RegisteredSkillAsset(
                skill_id=10,
                name="weather",
                git_path="center://public-weather",
                skill_uuid="00000000-0000-4000-8000-000000000010",
                sc_version_number="3.0.0",  # execution-time latest, not V2 payload
                mcp_dependencies=({"code": "mcp.v3"},),
            ),
        )


class _Latest:
    def latest_dependency_delta(self, *, env, skill_id):
        assert (env, skill_id) == ("pre", 10)
        return TrackLatestDependencyDelta(
            skill_version_id=102,
            claimed_mcp=frozenset({"mcp.v3"}),
            released_mcp=frozenset({"mcp.v1"}),
        )


class _Projector:
    def __init__(self) -> None:
        self.project_calls: list[dict] = []
        self.snapshots = [("latest-v3",), ("latest-v3",)]

    async def snapshot_skill_mappings(self, **_kwargs):
        return self.snapshots.pop(0)

    async def project(self, **kwargs):
        self.project_calls.append(kwargs)


def test_bot_task_rereads_latest_and_projects_skill_plus_dependency_delta_once() -> None:
    reader = _Reader(active=True)
    projector = _Projector()
    handler = BotTrackLatestReconcileTaskHandler(
        reader=reader,
        projector=projector,
        latest=_Latest(),
        env_provider=lambda: "pre",
    )

    outcome = handler.handle(
        {"owner_id": "owner-a", "bot_id": "bot-a", "skill_id": 10}
    )

    assert isinstance(outcome, Complete)
    assert reader.calls == 1
    assert projector.project_calls == [
        {
            "bot_id": "bot-a",
            "owner_id": "owner-a",
            "scope": ProjectionScope(
                skills=True,
                mcp=True,
                claimed_mcp=frozenset({"mcp.v3"}),
                released_mcp=frozenset({"mcp.v1"}),
            ),
        }
    ]


def test_bot_task_completes_without_projection_when_reader_no_longer_has_skill() -> None:
    reader = _Reader(active=False)
    projector = _Projector()
    handler = BotTrackLatestReconcileTaskHandler(
        reader=reader,
        projector=projector,
        latest=_Latest(),
        env_provider=lambda: "pre",
    )

    outcome = handler.handle(
        {"owner_id": "owner-a", "bot_id": "bot-a", "skill_id": 10}
    )

    assert isinstance(outcome, Complete)
    assert projector.project_calls == []


def test_bot_task_retries_when_complete_mapping_snapshot_drifts() -> None:
    projector = _Projector()
    projector.snapshots = [("latest-v3",), ("latest-v4",)]
    handler = BotTrackLatestReconcileTaskHandler(
        reader=_Reader(active=True),
        projector=projector,
        latest=_Latest(),
        env_provider=lambda: "pre",
    )

    outcome = handler.handle(
        {"owner_id": "owner-a", "bot_id": "bot-a", "skill_id": 10}
    )

    assert isinstance(outcome, Retry)


def test_bot_task_completes_when_candidate_bot_was_deleted() -> None:
    class _DeletedReader:
        def active_skill_assets(self, **_kwargs):
            raise LocalSkillNotFoundError()

    projector = _Projector()
    handler = BotTrackLatestReconcileTaskHandler(
        reader=_DeletedReader(),
        projector=projector,
        latest=_Latest(),
        env_provider=lambda: "pre",
    )

    outcome = handler.handle(
        {"owner_id": "owner-a", "bot_id": "deleted", "skill_id": 10}
    )

    assert isinstance(outcome, Complete)
    assert projector.project_calls == []
