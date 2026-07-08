"""Extended coverage for core/access/services/policy_service.py.

The existing tests/policy/test_policy_service.py cover the old service location.
These tests target the new location (core/access/services) and cover
allow/disallow/get_quota + missing branches in _get_effective_quota.
"""
from __future__ import annotations

from unittest.mock import patch

from agentclaw.community.core.access.models import AccessControlPolicyRecord, ConfigItemRecord
from agentclaw.community.core.access.services.policy_service import PolicyService


def _make_record(**kwargs):
    defaults = dict(id=1, entity_id="u1", entity_type="staff", policy=None)
    return AccessControlPolicyRecord(**{**defaults, **kwargs})


def _make_config(key, value) -> ConfigItemRecord:
    return ConfigItemRecord(config_key=key, config_value=value)


class FakeRepo:
    def __init__(
        self,
        policy_record=None,
        user_record=None,
        quota_config=None,
        total_limit_config=None,
        update_time_config=None,
        compete_count=0,
        active_device_count=0,
    ):
        self._policy_record = policy_record
        self._user_record = user_record
        self._quota_config = quota_config
        self._total_limit_config = total_limit_config
        self._update_time_config = update_time_config
        self._compete_count = compete_count
        self._active_device_count = active_device_count
        self.upserted_policy = None
        self.upserted_user = None

    def get_by_entity(self, *, entity_id, entity_type):
        return self._policy_record

    def upsert_policy(self, *, entity_id, entity_type, policy):
        self.upserted_policy = (entity_id, entity_type, policy)

    def get_config_by_key(self, *, config_key, category, env):
        if config_key == "daily_container_quota":
            return self._quota_config
        if config_key == "total_container_limit":
            return self._total_limit_config
        if config_key == "daily_container_update_time":
            return self._update_time_config
        return None

    def count_active_devices(self, *, env):
        return self._active_device_count

    def get_user_info(self, *, user_id, user_type):
        return self._user_record

    def list_users(self, *, user_type=None):
        return []

    def upsert_user_info(self, *, user_id, user_type, status):
        self.upserted_user = (user_id, user_type, status)

    def count_compete_users_after_time(self, *, start_time):
        return self._compete_count


class TestAllowDisallow:
    def test_allow_calls_upsert_with_policy_on(self):
        repo = FakeRepo()
        svc = PolicyService(repository=repo)
        svc.allow(entity_id="u1", entity_type="staff")
        assert repo.upserted_policy is not None
        entity_id, entity_type, policy = repo.upserted_policy
        import json
        assert json.loads(policy)["policy"] == "on"

    def test_disallow_calls_upsert_with_policy_off(self):
        repo = FakeRepo()
        svc = PolicyService(repository=repo)
        svc.disallow(entity_id="u1", entity_type="staff")
        assert repo.upserted_policy is not None
        entity_id, entity_type, policy = repo.upserted_policy
        import json
        assert json.loads(policy)["policy"] == "off"


class TestGetQuota:
    def test_returns_all_fields(self):
        repo = FakeRepo(
            quota_config=_make_config("daily_container_quota", "50"),
            total_limit_config=_make_config("total_container_limit", "200"),
            update_time_config=_make_config("daily_container_update_time", "09:00"),
            active_device_count=10,
            compete_count=5,
        )
        svc = PolicyService(repository=repo)
        with patch("agentclaw.community.core.access.services.policy_service.get_current_env", return_value="dev"):
            result = svc.get_quota()
        assert result["quota"] == 50
        assert result["totalLimit"] == 200
        assert result["activeCount"] == 10
        assert result["updateTime"] == "09:00"

    def test_missing_quota_record_returns_zero_quota(self):
        repo = FakeRepo(
            quota_config=None,
            update_time_config=_make_config("daily_container_update_time", "09:00"),
        )
        svc = PolicyService(repository=repo)
        with patch("agentclaw.community.core.access.services.policy_service.get_current_env", return_value="dev"):
            result = svc.get_quota()
        assert result["quota"] == 0

    def test_missing_update_record_returns_empty_string(self):
        repo = FakeRepo(
            quota_config=_make_config("daily_container_quota", "10"),
            update_time_config=None,
        )
        svc = PolicyService(repository=repo)
        with patch("agentclaw.community.core.access.services.policy_service.get_current_env", return_value="dev"):
            result = svc.get_quota()
        assert result["updateTime"] == ""


class TestGetEffectiveQuota:
    def test_no_quota_record_returns_zero(self):
        repo = FakeRepo(quota_config=None)
        svc = PolicyService(repository=repo)
        with patch("agentclaw.community.core.access.services.policy_service.get_current_env", return_value="dev"):
            assert svc._get_effective_quota("dev") == 0

    def test_invalid_quota_value_returns_zero(self):
        repo = FakeRepo(quota_config=_make_config("daily_container_quota", "not_a_number"))
        svc = PolicyService(repository=repo)
        assert svc._get_effective_quota("dev") == 0

    def test_no_total_limit_returns_daily_quota(self):
        repo = FakeRepo(
            quota_config=_make_config("daily_container_quota", "30"),
            total_limit_config=None,
        )
        svc = PolicyService(repository=repo)
        assert svc._get_effective_quota("dev") == 30

    def test_invalid_total_limit_returns_daily_quota(self):
        repo = FakeRepo(
            quota_config=_make_config("daily_container_quota", "30"),
            total_limit_config=_make_config("total_container_limit", "bad"),
        )
        svc = PolicyService(repository=repo)
        assert svc._get_effective_quota("dev") == 30

    def test_effective_quota_is_min_of_daily_and_available(self):
        # daily_quota=50, total_limit=100, active=90 → available=10 → effective=10
        repo = FakeRepo(
            quota_config=_make_config("daily_container_quota", "50"),
            total_limit_config=_make_config("total_container_limit", "100"),
            active_device_count=90,
        )
        svc = PolicyService(repository=repo)
        assert svc._get_effective_quota("dev") == 10

    def test_effective_quota_clipped_to_zero_when_oversubscribed(self):
        # active > total_limit → available is negative → effective=0
        repo = FakeRepo(
            quota_config=_make_config("daily_container_quota", "50"),
            total_limit_config=_make_config("total_container_limit", "5"),
            active_device_count=10,
        )
        svc = PolicyService(repository=repo)
        assert svc._get_effective_quota("dev") == 0


class TestIsPolicyOnOff:
    def test_is_policy_on_with_on(self):
        import json
        repo = FakeRepo()
        svc = PolicyService(repository=repo)
        assert svc._is_policy_on(json.dumps({"policy": "on"})) is True

    def test_is_policy_on_with_off(self):
        import json
        repo = FakeRepo()
        svc = PolicyService(repository=repo)
        assert svc._is_policy_on(json.dumps({"policy": "off"})) is False

    def test_is_policy_on_with_none(self):
        repo = FakeRepo()
        svc = PolicyService(repository=repo)
        assert svc._is_policy_on(None) is False

    def test_is_policy_on_with_invalid_json(self):
        repo = FakeRepo()
        svc = PolicyService(repository=repo)
        assert svc._is_policy_on("not-json") is False

    def test_is_policy_off_with_off(self):
        import json
        repo = FakeRepo()
        svc = PolicyService(repository=repo)
        assert svc._is_policy_off(json.dumps({"policy": "off"})) is True

    def test_is_policy_off_with_none(self):
        repo = FakeRepo()
        svc = PolicyService(repository=repo)
        assert svc._is_policy_off(None) is False

    def test_is_policy_off_with_invalid_json(self):
        repo = FakeRepo()
        svc = PolicyService(repository=repo)
        assert svc._is_policy_off("not-json") is False
