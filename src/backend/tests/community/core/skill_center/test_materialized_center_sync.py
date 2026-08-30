"""Exact-version synchronization of already materialized SC Public assets."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import SQLAlchemyError

from agentclaw.community.core.repository.skill_center_reference_types import (
    MaterializedPublicCenterAsset,
    PublicCenterVersionTarget,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    PublishedMaterializedSkillVersion,
)
from agentclaw.community.core.skill_center.public_center_identity import (
    PublicCenterSkillIdentity,
)
from agentclaw.community.core.skill_center.services.skill_center_sync_service import (
    SkillCenterSyncInProgressError,
    SkillCenterSyncService,
    SkillCenterSyncUnavailableError,
)
from agentclaw.community.plugin_api.cache import CacheLockInfrastructureError
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
        identity = PublicCenterSkillIdentity.derive(
            tenant="teamclaw", env="pre", skill_code="public-updated"
        )
        assert kwargs["locator"] == identity.locator
        assert kwargs["skill_uuid"] == identity.skill_uuid
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
    def __init__(self, *, lock_value="lock-token", renew_ok=True) -> None:
        self.lock_value = lock_value
        self.renew_ok = renew_ok
        self.released = []
        self.renewed = []

    def acquire_lock_strict(self, key, ttl):
        return self.lock_value

    def release_lock(self, key, value):
        self.released.append((key, value))
        return True

    def renew_lock_strict(self, key, value, ttl):
        self.renewed.append((key, value, ttl))
        return self.renew_ok


class _UnavailableCache(_Cache):
    def acquire_lock_strict(self, key, ttl):
        raise CacheLockInfrastructureError("redis endpoint details")


class _RenewUnavailableCache(_Cache):
    def renew_lock_strict(self, key, value, ttl):
        raise CacheLockInfrastructureError("redis renew endpoint details")


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
    assert len(service._cache.renewed) == 2


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


def test_manual_sync_maps_cache_outage_to_stable_unavailable_error() -> None:
    service = SkillCenterSyncService(
        assets=_Assets(),
        gateway=_Gateway(),
        materializer=_Materializer(),
        track_latest=_TrackLatest(),
        cache=_UnavailableCache(),
        env_provider=lambda: "pre",
    )

    with pytest.raises(
        SkillCenterSyncUnavailableError, match="SYNC_COORDINATOR_UNAVAILABLE"
    ):
        service.sync()


def test_startup_cache_outage_is_deferred_to_periodic_retry() -> None:
    service = SkillCenterSyncService(
        assets=_Assets(),
        gateway=_Gateway(),
        materializer=_Materializer(),
        track_latest=_TrackLatest(),
        cache=_UnavailableCache(),
        env_provider=lambda: "pre",
    )

    summary = asyncio.run(service.sync_bootstrap())

    assert summary.scanned == summary.failed == 0


def test_manual_sync_maps_cache_renewal_outage_to_stable_unavailable_error() -> None:
    service = SkillCenterSyncService(
        assets=_Assets(),
        gateway=_Gateway(),
        materializer=_Materializer(),
        track_latest=_TrackLatest(),
        cache=_RenewUnavailableCache(),
        env_provider=lambda: "pre",
    )

    with pytest.raises(SkillCenterSyncUnavailableError):
        service.sync()


def test_sync_fails_closed_when_its_distributed_lease_is_lost() -> None:
    materializer = _Materializer()
    service = SkillCenterSyncService(
        assets=_Assets(),
        gateway=_Gateway(),
        materializer=materializer,
        track_latest=_TrackLatest(),
        cache=_Cache(renew_ok=False),
        env_provider=lambda: "pre",
    )

    with pytest.raises(SkillCenterSyncInProgressError, match="SYNC_LOCK_LOST"):
        service.sync()

    assert materializer.calls == []


def test_published_exact_version_reensures_track_latest_before_unchanged() -> None:
    class _PublishedAssets(_Assets):
        def list_materialized_public_assets(self, *, env):
            return super().list_materialized_public_assets(env=env)[:1]

        def ensure_public_version(self, **kwargs):
            target = super().ensure_public_version(**kwargs)
            return PublicCenterVersionTarget(
                skill_id=target.skill_id,
                skill_version_id=target.skill_version_id,
                status="PUBLISHED",
            )

    materializer = _Materializer()
    track_latest = _TrackLatest()
    service = SkillCenterSyncService(
        assets=_PublishedAssets(),
        gateway=_Gateway(),
        materializer=materializer,
        track_latest=track_latest,
        cache=_Cache(),
        env_provider=lambda: "pre",
    )

    summary = service.sync()

    assert summary.updated == 0
    assert summary.unchanged == 1
    assert len(materializer.calls) == 1
    assert len(track_latest.calls) == 1


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


@pytest.mark.parametrize(
    "error",
    [SQLAlchemyError("database unavailable"), RuntimeError("database unavailable")],
)
def test_sync_propagates_persistence_failure_instead_of_returning_success(
    error: Exception,
) -> None:
    class _BrokenAssets(_Assets):
        def ensure_public_version(self, **_kwargs):
            raise error

    cache = _Cache()
    service = SkillCenterSyncService(
        assets=_BrokenAssets(),
        gateway=_Gateway(),
        materializer=_Materializer(),
        track_latest=_TrackLatest(),
        cache=cache,
        env_provider=lambda: "pre",
    )

    with pytest.raises(type(error), match="database unavailable"):
        service.sync()

    assert cache.released == [
        ("skill-center-public-sync:pre", "lock-token")
    ]
