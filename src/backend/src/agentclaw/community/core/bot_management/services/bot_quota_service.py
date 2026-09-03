"""Space-scoped Bot quota policy and mutation serialization."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from typing import Iterator, TYPE_CHECKING

from injector import inject

from agentclaw.community.core.access.policy_service_protocol import (
    PolicyServiceProtocol,
)
from agentclaw.community.core.bot_management.bot_quota import (
    BotQuotaBusyError,
    BotQuotaConfigurationError,
    BotQuotaError,
    BotQuotaExceededError,
    BotQuotaScope,
    BotQuotaSnapshot,
    BotQuotaUnavailableError,
)
from agentclaw.community.core.bot_management.bot_quota_service_protocol import (
    BotQuotaServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import (
    CacheLockInfrastructureError,
    CachePlugin,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

if TYPE_CHECKING:
    from agentclaw.community.di import config as cfg


logger = get_logger()

TEAM_DEFAULT_BOT_CEILING = 20
_QUOTA_LOCK_TTL_SECONDS = 30


class BotQuotaService(BotQuotaServiceProtocol):
    @inject
    def __init__(
        self,
        repository: BotRepository,
        allocation_config: "cfg.DeviceAllocationConfig",
        policy_service: PolicyServiceProtocol,
        cache: CachePlugin,
        space_access: SpaceAccessServiceProtocol,
    ) -> None:
        self._repository = repository
        self._allocation_config = allocation_config
        self._policy_service = policy_service
        self._cache = cache
        self._space_access = space_access

    def inspect(self, *, owner_id: str, space_id: int | None) -> BotQuotaSnapshot:
        try:
            return self._inspect(self._scope(owner_id=owner_id, space_id=space_id))
        except BotQuotaError:
            raise
        except Exception as exc:
            raise BotQuotaUnavailableError("Bot quota state is unavailable") from exc

    def _inspect(self, scope: BotQuotaScope) -> BotQuotaSnapshot:
        ceiling = self._ceiling(scope)
        if scope.space_type is SpaceType.PERSONAL:
            used = self._repository.count_cloud_bots_in_personal_space(
                owner_id=scope.owner_id,
                personal_space_id=scope.space_id,
            )
        else:
            if scope.space_id is None:
                raise BotQuotaConfigurationError("Team Space requires a numeric id")
            used = self._repository.count_cloud_bots_by_space(space_id=scope.space_id)
        return BotQuotaSnapshot(scope=scope, ceiling=ceiling, used=int(used))

    def assert_can_add(
        self,
        *,
        owner_id: str,
        space_id: int | None,
    ) -> BotQuotaSnapshot:
        return self._assert_available(
            self.inspect(owner_id=owner_id, space_id=space_id)
        )

    @contextmanager
    def guard_add(
        self,
        *,
        owner_id: str,
        space_id: int | None,
    ) -> Iterator[BotQuotaSnapshot]:
        try:
            scope = self._scope(owner_id=owner_id, space_id=space_id)
        except BotQuotaError:
            raise
        except Exception as exc:
            raise BotQuotaUnavailableError("Bot quota state is unavailable") from exc
        with self._locked(scope):
            try:
                snapshot = self._assert_available(self._inspect(scope))
            except BotQuotaError:
                raise
            except Exception as exc:
                raise BotQuotaUnavailableError(
                    "Bot quota state is unavailable"
                ) from exc
            yield snapshot

    def set_team_ceiling(self, *, space_id: int, ceiling: int) -> int:
        if ceiling <= 0:
            raise BotQuotaConfigurationError("ceiling must be positive")
        scope = self._team_scope(space_id)
        with self._locked(scope):
            self._policy_service.set_bots_ceiling(
                entity_type="space",
                entity_id=str(space_id),
                ceiling=ceiling,
            )
        return ceiling

    def reset_team_ceiling(self, *, space_id: int) -> int:
        scope = self._team_scope(space_id)
        with self._locked(scope):
            self._policy_service.clear_bots_ceiling(
                entity_type="space", entity_id=str(space_id)
            )
            return self._policy_service.get_bots_ceiling(
                entity_type="space",
                entity_id=str(space_id),
                default=TEAM_DEFAULT_BOT_CEILING,
            )

    def _ceiling(self, scope: BotQuotaScope) -> int:
        if scope.space_type is SpaceType.TEAM:
            if scope.space_id is None:
                raise BotQuotaConfigurationError("Team Space requires a numeric id")
            return self._policy_service.get_bots_ceiling(
                entity_type="space",
                entity_id=str(scope.space_id),
                default=TEAM_DEFAULT_BOT_CEILING,
            )

        default = 0
        try:
            default = int(self._allocation_config.max_devices_per_entity)
        except (TypeError, ValueError):
            pass
        return self._policy_service.get_bots_ceiling(
            entity_type="staff",
            entity_id=scope.owner_id,
            default=default,
        )

    def _team_scope(self, space_id: int) -> BotQuotaScope:
        scope = self._scope(owner_id="", space_id=space_id)
        if scope.space_type is not SpaceType.TEAM:
            raise BotQuotaConfigurationError(
                "only Team Space ceilings can be configured here"
            )
        return scope

    def _scope(self, *, owner_id: str, space_id: int | None) -> BotQuotaScope:
        if space_id is None:
            return BotQuotaScope(
                owner_id=owner_id,
                space_id=None,
                space_name="Personal",
                space_type=SpaceType.PERSONAL,
            )
        space = self._space_access.require_space(space_id=space_id)
        if (
            owner_id
            and space.space_type is SpaceType.PERSONAL
            and space.personal_owner_id != owner_id
        ):
            raise BotQuotaConfigurationError("Personal Space belongs to another user")
        return BotQuotaScope(
            owner_id=owner_id,
            space_id=space.id,
            space_name=space.name,
            space_type=space.space_type,
        )

    @staticmethod
    def _assert_available(snapshot: BotQuotaSnapshot) -> BotQuotaSnapshot:
        if snapshot.ceiling > 0 and snapshot.used >= snapshot.ceiling:
            raise BotQuotaExceededError(snapshot)
        return snapshot

    @contextmanager
    def _locked(self, scope: BotQuotaScope) -> Iterator[None]:
        lock_key = self._lock_key(scope)
        try:
            lock_value = self._cache.acquire_lock_strict(
                lock_key, ttl=_QUOTA_LOCK_TTL_SECONDS
            )
        except CacheLockInfrastructureError as exc:
            raise BotQuotaUnavailableError(
                "Bot quota coordinator is unavailable"
            ) from exc
        if lock_value is None:
            raise BotQuotaBusyError("Bot quota mutation is in progress")
        try:
            yield
        finally:
            try:
                if not self._cache.release_lock(lock_key, lock_value):
                    logger.warning(
                        "[bot_quota] quota lock was no longer owned key=%s",
                        lock_key,
                    )
            except Exception:
                logger.exception(
                    "[bot_quota] failed to release quota lock key=%s",
                    lock_key,
                )

    @staticmethod
    def _lock_key(scope: BotQuotaScope) -> str:
        raw = "\0".join(
            (
                get_current_avernet_tenant(),
                get_current_env(),
                scope.lock_scope,
            )
        )
        digest = sha256(raw.encode("utf-8")).hexdigest()
        return f"bot-quota:{digest}"
