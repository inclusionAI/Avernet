"""Reconcile only already-materialized SC Public assets to exact latest Versions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import time

from agentclaw.community.core.repository.protocols.skill_center_reference import (
    SkillCenterReferenceRepositoryProtocol,
)
from agentclaw.community.core.repository.skill_center_reference_types import (
    MaterializedPublicCenterAsset,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    SkillVersionMaterializationError,
    SkillVersionMaterializationRequest,
    SkillVersionMaterializerProtocol,
)
from agentclaw.community.core.skill_center.skill_center_gateway_service_protocol import (
    SkillCenterGatewayServiceProtocol,
)
from agentclaw.community.core.skill_center.public_center_identity import (
    PublicCenterSkillIdentity,
)
from agentclaw.community.core.skill_center.skill_center_sync_contract import (
    SkillCenterSyncFailure,
    SkillCenterSyncInProgressError,
    SkillCenterSyncUnavailableError,
    SkillCenterSyncSummary,
)
from agentclaw.community.core.skill_center.skill_center_sync_service_protocol import (
    SkillCenterSyncServiceProtocol,
)
from agentclaw.community.core.skill_center.track_latest_service_protocol import (
    TrackLatestServiceProtocol,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import (
    CacheLockInfrastructureError,
    CachePlugin,
)
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterGatewayError,
    SkillCenterPublicSkillDetailRequest,
    SkillCenterReadScope,
    SkillCenterVersionListRequest,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


logger = get_logger()
_SYNC_LOCK_TTL_SECONDS = 30 * 60


class SkillCenterSyncService(LifecycleBase, SkillCenterSyncServiceProtocol):
    """The single periodic/manual shell around exact-version materialization."""

    def __init__(
        self,
        *,
        assets: SkillCenterReferenceRepositoryProtocol,
        gateway: SkillCenterGatewayServiceProtocol,
        materializer: SkillVersionMaterializerProtocol,
        track_latest: TrackLatestServiceProtocol,
        cache: CachePlugin,
        env_provider: Callable[[], str] = get_current_env,
        interval_seconds: int = 30 * 60,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval_seconds < 1:
            raise ValueError("interval_seconds must be positive")
        self._assets = assets
        self._gateway = gateway
        self._materializer = materializer
        self._track_latest = track_latest
        self._cache = cache
        self._env_provider = env_provider
        self._interval_seconds = interval_seconds
        self._monotonic = monotonic
        self._periodic_task: asyncio.Task | None = None

    async def startup(self) -> None:
        await self.sync_bootstrap()
        await self.start_periodic_sync()

    async def shutdown(self) -> None:
        await self.stop_periodic_sync()

    def sync(self) -> SkillCenterSyncSummary:
        env = self._env_provider()
        lock_key = f"skill-center-public-sync:{env}"
        # Start the local budget before SET NX EX. The cache TTL starts while
        # acquiring the lock, so this may shorten a batch but cannot extend it
        # beyond the actual cross-worker lease.
        lease_started = self._monotonic()
        try:
            lock_value = self._cache.acquire_lock_strict(
                lock_key, ttl=_SYNC_LOCK_TTL_SECONDS
            )
        except CacheLockInfrastructureError as exc:
            raise SkillCenterSyncUnavailableError("SYNC_COORDINATOR_UNAVAILABLE") from exc
        if lock_value is None:
            raise SkillCenterSyncInProgressError("SYNC_IN_PROGRESS")
        lease_deadline = lease_started + _SYNC_LOCK_TTL_SECONDS
        # This cache adapter has no atomic compare-and-delete, so this fixed
        # lease is not released early. Its TTL is the only safe cross-worker
        # handoff point.
        assets = self._assets.list_materialized_public_assets(env=env)
        scanned = 0
        updated = 0
        unchanged = 0
        failures: list[SkillCenterSyncFailure] = []
        for asset in assets:
            # The cache adapter supports fixed-TTL acquisition but not
            # token-checked renewal, so this coordinator deliberately follows
            # GitSync's fixed-TTL lease pattern. Exact materialization is
            # idempotent at an asset boundary; stop before beginning a new
            # asset once the lease has expired.
            if self._monotonic() >= lease_deadline:
                logger.warning(
                    "[SkillCenterSync] lease budget exhausted; deferring "
                    "remaining public assets to the next reconciliation"
                )
                break
            scanned += 1
            try:
                changed = self._sync_asset(env=env, asset=asset)
                if changed:
                    updated += 1
                else:
                    unchanged += 1
            except (
                SkillCenterGatewayError,
                SkillVersionMaterializationError,
                _SkillCenterSyncAssetError,
                ValueError,
            ) as exc:
                failures.append(
                    SkillCenterSyncFailure(
                        skill_id=str(asset.skill_id),
                        skill_code=asset.skill_code,
                        error_code=_sync_error_code(exc),
                    )
                )
        return SkillCenterSyncSummary(
            scanned=scanned,
            updated=updated,
            unchanged=unchanged,
            failed=len(failures),
            failures=tuple(failures),
        )

    async def sync_bootstrap(self) -> SkillCenterSyncSummary:
        try:
            return await asyncio.to_thread(self.sync)
        except SkillCenterSyncInProgressError:
            return SkillCenterSyncSummary(0, 0, 0, 0, ())
        except SkillCenterSyncUnavailableError:
            # Startup/periodic reconciliation is best effort and level-triggered;
            # cache recovery will be observed by the next scheduled pass.
            logger.exception(
                "[SkillCenterSync] startup reconciliation coordinator unavailable"
            )
            return SkillCenterSyncSummary(0, 0, 0, 0, ())

    async def start_periodic_sync(self) -> None:
        if self._periodic_task is None or self._periodic_task.done():
            self._periodic_task = asyncio.create_task(self._periodic_loop())

    async def stop_periodic_sync(self) -> None:
        if self._periodic_task is None:
            return
        self._periodic_task.cancel()
        try:
            await self._periodic_task
        except asyncio.CancelledError:
            pass
        self._periodic_task = None

    async def _periodic_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                await asyncio.to_thread(self.sync)
            except SkillCenterSyncInProgressError:
                continue
            except Exception:
                # A periodic reconciliation is self-healing and level-triggered:
                # one infrastructure failure must not permanently stop future runs.
                logger.exception("[SkillCenterSync] periodic reconciliation failed")

    def _sync_asset(
        self, *, env: str, asset: MaterializedPublicCenterAsset
    ) -> bool:
        detail = self._gateway.get_public_skill(
            SkillCenterPublicSkillDetailRequest(asset.skill_code)
        )
        if detail is None or detail.skill_id is None or not detail.latest_version_number:
            raise _SkillCenterSyncAssetError(
                "SC public Skill has no consumable latest Version"
            )
        versions = self._gateway.list_versions(
            SkillCenterVersionListRequest(
                skill_code=asset.skill_code,
                scope=SkillCenterReadScope.PUBLIC,
            )
        )
        exact = next(
            (
                version
                for version in versions
                if version.version_number == detail.latest_version_number
            ),
            None,
        )
        if exact is None or exact.version_id is None:
            raise _SkillCenterSyncAssetError(
                "SC latest public Version has no exact identity"
            )
        identity = PublicCenterSkillIdentity.derive(
            tenant=get_current_avernet_tenant(),
            env=env,
            skill_code=asset.skill_code,
        )
        target = self._assets.ensure_public_version(
            env=env,
            actor_id="system:skill-center-sync",
            locator=identity.locator,
            skill_uuid=identity.skill_uuid,
            skill_name=detail.skill_name,
            description=detail.description,
            sc_skill_id=_positive_int(detail.skill_id),
            sc_version_number=exact.version_number,
            sc_version_id=_positive_int(exact.version_id),
        )
        published = self._materializer.materialize(
            SkillVersionMaterializationRequest(
                env=env,
                skill_id=target.skill_id,
                skill_version_id=target.skill_version_id,
                scope=SkillCenterReadScope.PUBLIC,
            )
        )
        # Re-ensure even when the exact Version was already PUBLISHED. A crash
        # may have committed materialization before durable Track Latest enqueue;
        # periodic/manual sync is the level-triggered repair for that window.
        self._track_latest.version_published(published)
        return target.status != "PUBLISHED"


def _positive_int(value: object) -> int:
    parsed = int(value)
    if parsed < 1:
        raise ValueError("Skill Center identity must be positive")
    return parsed


class _SkillCenterSyncAssetError(RuntimeError):
    """One SC asset cannot currently resolve to a consumable exact Version."""


def _sync_error_code(error: Exception) -> str:
    if isinstance(error, SkillVersionMaterializationError):
        return "MATERIALIZATION_FAILED"
    return "SC_MARKET_UNAVAILABLE"


__all__ = [
    "SkillCenterSyncInProgressError",
    "SkillCenterSyncUnavailableError",
    "SkillCenterSyncService",
]
