"""Exact-version synchronization of already materialized SC Public assets."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from agentclaw.community.core.repository.skill_center_reference_types import (
    MaterializedPublicCenterAsset,
    PublicCenterVersionTarget,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    PublishedMaterializedSkillVersion,
)
from agentclaw.community.core.skill_center.services.skill_center_sync_service import (
    SkillCenterSyncInProgressError,
    SkillCenterSyncService,
)
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterAccessLevel,
    SkillCenterGatewayError,
    SkillCenterGatewayErrorCode,
    SkillCenterSkill,
    SkillCenterVersion,
)


class _Assets:
    def list_materialized_public_assets(self, *, env):
        assert env == "pre"
        return (
            MaterializedPublicCenterAsset(
                skill_id=10,
                skill_code="public-updated",
                name="updated",
                description=None,
            ),
            MaterializedPublicCenterAsset(
                skill_id=20,
                skill_code="public-failed",
                name="failed",
                description=None,
            ),
        )

    def ensure_public_version(self, **kwargs):
        assert kwargs["skill_code"] == "public-updated"
        return PublicCenterVersionTarget(
            skill_id=10, skill_version_id=102, status="MATERIALIZING"
        )


class _Gateway:
    def get_public_skill(self, request):
        if request.skill_code == "public-failed":
            raise SkillCenterGatewayError(
                SkillCenterGatewayErrorCode.UNAVAILABLE, "SC unavailable"
            )
        return SkillCenterSkill(
            skill_code=request.skill_code,
            skill_name="updated",
            access_level=SkillCenterAccessLevel.PUBLIC,
            skill_id="9001",
            latest_version_number="2.0.0",
        )

    def list_versions(self, request):
        return (SkillCenterVersion(version_number="2.0.0", version_id="10002"),)


class _Materializer:
    def __init__(self) -> None:
        self.calls = []

    def materialize(self, request):
        self.calls.append(request)
        return PublishedMaterializedSkillVersion(
            skill_version_id=102,
            skill_id=10,
            version_ordinal=2,
            status="PUBLISHED",
            skill_uuid="00000000-0000-4000-8000-000000000010",
            sc_version_number="2.0.0",
            sc_skill_id=9001,
            sc_version_id=10002,
            name="updated",
            description=None,
            metadata_json='{"mcp_dependencies":[]}',
            published_at=datetime(2026, 8, 30, tzinfo=UTC),
        )


class _TrackLatest:
    def __init__(self) -> None:
        self.calls = []

    def version_published(self, version):
        self.calls.append(version)


class _Cache:
    def __init__(self, *, lock_value="lock-token") -> None:
        self.lock_value = lock_value
        self.released = []

    def acquire_lock_strict(self, key, ttl):
        return self.lock_value

    def release_lock(self, key, value):
        self.released.append((key, value))
        return True


def test_sync_continues_after_one_failure_and_tracks_only_new_published_version() -> None:
    materializer = _Materializer()
    track_latest = _TrackLatest()
    service = SkillCenterSyncService(
        assets=_Assets(),
        gateway=_Gateway(),
        materializer=materializer,
        track_latest=track_latest,
        cache=_Cache(),
        env_provider=lambda: "pre",
        interval_seconds=1800,
    )

    summary = service.sync()

    assert summary.scanned == 2
    assert summary.updated == 1
    assert summary.unchanged == 0
    assert summary.failed == 1
    assert len(summary.failures) == 1
    assert summary.failures[0].skill_id == "20"
    assert len(materializer.calls) == 1
    assert len(track_latest.calls) == 1


def test_manual_sync_returns_stable_conflict_when_distributed_lock_is_held() -> None:
    service = SkillCenterSyncService(
        assets=_Assets(),
        gateway=_Gateway(),
        materializer=_Materializer(),
        track_latest=_TrackLatest(),
        cache=_Cache(lock_value=None),
        env_provider=lambda: "pre",
        interval_seconds=1800,
    )

    with pytest.raises(SkillCenterSyncInProgressError):
        service.sync()


def test_lifecycle_bootstrap_reconciles_once_then_starts_and_stops_periodic_task() -> None:
    cache = _Cache()
    service = SkillCenterSyncService(
        assets=_Assets(),
        gateway=_Gateway(),
        materializer=_Materializer(),
        track_latest=_TrackLatest(),
        cache=cache,
        env_provider=lambda: "pre",
        interval_seconds=3600,
    )

    async def run_lifecycle() -> None:
        await service.startup()
        assert service._periodic_task is not None
        assert not service._periodic_task.done()
        await service.shutdown()
        assert service._periodic_task is None

    asyncio.run(run_lifecycle())
    assert cache.released == [
        ("skill-center-public-sync:pre", "lock-token")
    ]
