"""Space-scoped Bot quota policy, counting, and mutation serialization."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.bot_quota import (
    BotQuotaBusyError,
    BotQuotaConfigurationError,
    BotQuotaExceededError,
    BotQuotaUnavailableError,
)
from agentclaw.community.core.bot_management.services.bot_quota_service import (
    TEAM_DEFAULT_BOT_CEILING,
    BotQuotaService,
)
from agentclaw.community.core.spaces.models import SpaceRecord, SpaceType
from agentclaw.community.plugin_api.cache import CacheLockInfrastructureError
from agentclaw.community.plugins.local.cache import MemoryCachePlugin

pytestmark = pytest.mark.unit


def _space(
    *, space_id: int = 42, space_type: SpaceType = SpaceType.TEAM
) -> SpaceRecord:
    now = datetime(2026, 9, 3, tzinfo=UTC)
    return SpaceRecord(
        id=space_id,
        space_code=f"spc-{space_id}",
        space_type=space_type,
        name="Team" if space_type is SpaceType.TEAM else "Personal",
        personal_owner_id="u1" if space_type is SpaceType.PERSONAL else None,
        env="dev",
        created_by="u1",
        updated_by="u1",
        gmt_created=now,
        gmt_modified=now,
    )


def _service(*, cache=None, max_personal: int = 5):
    repository = MagicMock()
    policy = MagicMock()
    policy.get_bots_ceiling.side_effect = lambda *, default, **_kwargs: default
    if cache is None:
        cache = MagicMock()
        cache.acquire_lock_strict.return_value = "lease-token"
    access = MagicMock()
    access.require_space.return_value = _space()
    service = BotQuotaService(
        repository=repository,
        allocation_config=SimpleNamespace(max_devices_per_entity=max_personal),
        policy_service=policy,
        cache=cache,
        space_access=access,
    )
    return service, repository, policy, cache, access


def test_personal_space_reuses_the_owner_ceiling_and_counts_legacy_rows():
    service, repository, policy, _cache, access = _service(max_personal=6)
    access.require_space.return_value = _space(
        space_id=7, space_type=SpaceType.PERSONAL
    )
    repository.count_cloud_bots_in_personal_space.return_value = 4

    snapshot = service.inspect(owner_id="u1", space_id=7)

    assert (snapshot.ceiling, snapshot.used) == (6, 4)
    policy.get_bots_ceiling.assert_called_once_with(
        entity_type="staff", entity_id="u1", default=6
    )
    repository.count_cloud_bots_in_personal_space.assert_called_once_with(
        owner_id="u1",
        personal_space_id=7,
    )


def test_team_space_defaults_to_twenty_and_counts_every_owner():
    service, repository, policy, _cache, _access = _service()
    repository.count_cloud_bots_by_space.return_value = 7

    snapshot = service.inspect(owner_id="u1", space_id=42)

    assert snapshot.ceiling == TEAM_DEFAULT_BOT_CEILING == 20
    assert snapshot.used == 7
    policy.get_bots_ceiling.assert_called_once_with(
        entity_type="space", entity_id="42", default=20
    )
    repository.count_cloud_bots_by_space.assert_called_once_with(space_id=42)


def test_team_space_uses_its_individual_override():
    service, repository, policy, _cache, _access = _service()
    repository.count_cloud_bots_by_space.return_value = 9
    policy.get_bots_ceiling.return_value = 30
    policy.get_bots_ceiling.side_effect = None

    snapshot = service.inspect(owner_id="u1", space_id=42)

    assert (snapshot.ceiling, snapshot.used) == (30, 9)


def test_capacity_error_carries_only_actionable_space_facts():
    service, repository, _policy, _cache, _access = _service()
    repository.count_cloud_bots_by_space.return_value = 20

    with pytest.raises(BotQuotaExceededError) as raised:
        service.assert_can_add(owner_id="u1", space_id=42)

    assert raised.value.as_payload() == {
        "space_id": "42",
        "space_name": "Team",
        "space_type": "TEAM",
        "ceiling": 20,
        "used": 20,
    }


def test_guard_recounts_under_the_scope_lock_and_releases_it():
    service, repository, _policy, cache, _access = _service()
    repository.count_cloud_bots_by_space.return_value = 3

    with service.guard_add(owner_id="u1", space_id=42) as snapshot:
        assert snapshot.used == 3

    cache.acquire_lock_strict.assert_called_once()
    lock_key = cache.acquire_lock_strict.call_args.args[0]
    assert lock_key.startswith("bot-quota:")
    cache.release_lock.assert_called_once_with(lock_key, "lease-token")
    repository.count_cloud_bots_by_space.assert_called_once_with(space_id=42)


def test_a_second_mutation_on_the_same_scope_is_busy():
    cache = MemoryCachePlugin()
    service, repository, _policy, _cache, _access = _service(cache=cache)
    repository.count_cloud_bots_by_space.return_value = 0

    with service.guard_add(owner_id="u1", space_id=42):
        with pytest.raises(BotQuotaBusyError):
            with service.guard_add(owner_id="u1", space_id=42):
                pass


def test_cache_outage_fails_closed_instead_of_looking_like_contention():
    service, _repository, _policy, cache, _access = _service()
    cache.acquire_lock_strict.side_effect = CacheLockInfrastructureError("down")

    with pytest.raises(BotQuotaUnavailableError):
        with service.guard_add(owner_id="u1", space_id=None):
            pass

    cache.release_lock.assert_not_called()


@pytest.mark.parametrize("failure_source", ["policy", "repository"])
def test_quota_reads_fail_closed_when_state_is_unavailable(failure_source):
    service, repository, policy, _cache, _access = _service()
    if failure_source == "policy":
        policy.get_bots_ceiling.side_effect = RuntimeError("policy unavailable")
    else:
        repository.count_cloud_bots_by_space.side_effect = RuntimeError(
            "database unavailable"
        )

    with pytest.raises(BotQuotaUnavailableError):
        service.inspect(owner_id="u1", space_id=42)


def test_operator_override_only_accepts_a_real_team_space():
    service, _repository, policy, _cache, access = _service()

    assert service.set_team_ceiling(space_id=42, ceiling=25) == 25
    access.require_space.assert_called_once_with(space_id=42)
    policy.set_bots_ceiling.assert_called_once_with(
        entity_type="space", entity_id="42", ceiling=25
    )

    access.require_space.return_value = _space(
        space_id=7, space_type=SpaceType.PERSONAL
    )
    with pytest.raises(BotQuotaConfigurationError):
        service.set_team_ceiling(space_id=7, ceiling=8)


def test_reset_removes_only_the_override_and_returns_the_team_default():
    service, _repository, policy, _cache, _access = _service()

    ceiling = service.reset_team_ceiling(space_id=42)

    assert ceiling == 20
    policy.clear_bots_ceiling.assert_called_once_with(
        entity_type="space", entity_id="42"
    )
