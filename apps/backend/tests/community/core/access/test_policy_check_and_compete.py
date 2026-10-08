"""Tests for PolicyService.check() and _try_compete() — covers lines 37-54, 78-110."""
from __future__ import annotations

from unittest.mock import patch


from agentclaw.community.core.access.models import AccessControlPolicyRecord, ConfigItemRecord, UserInfoRecord
from agentclaw.community.core.access.services.policy_service import PolicyService


def _policy_record(policy: str | None) -> AccessControlPolicyRecord:
    return AccessControlPolicyRecord(id=1, entity_id="u1", entity_type="staff", policy=policy)


def _user_record(status: str) -> UserInfoRecord:
    return UserInfoRecord(id=2, user_id="u1", user_type="COMPETE", status=status)


def _config(key: str, value: str) -> ConfigItemRecord:
    return ConfigItemRecord(config_key=key, config_value=value)


class FakeRepo:
    def __init__(self, *, policy=None, user=None, quota=None, total_limit=None,
                 update_time=None, compete_count=0, active_count=0):
        self._policy = policy
        self._user = user
        self._quota = quota
        self._total_limit = total_limit
        self._update_time = update_time
        self._compete_count = compete_count
        self._active_count = active_count
        self.upserted_user = None

    def get_by_entity(self, *, entity_id, entity_type):
        return self._policy

    def upsert_policy(self, *, entity_id, entity_type, policy):
        pass

    def get_config_by_key(self, *, config_key, category, env):
        if config_key == "daily_container_quota":
            return self._quota
        if config_key == "total_container_limit":
            return self._total_limit
        if config_key == "daily_container_update_time":
            return self._update_time
        return None

    def count_active_devices(self, *, env):
        return self._active_count

    def get_user_info(self, *, user_id, user_type):
        return self._user

    def list_users(self, *, user_type=None):
        return []

    def upsert_user_info(self, *, user_id, user_type, status):
        self.upserted_user = (user_id, user_type, status)

    def count_compete_users_after_time(self, *, start_time):
        return self._compete_count


class TestCheckMethod:
    def test_whitelist_policy_on_returns_true(self):
        import json
        repo = FakeRepo(policy=_policy_record(json.dumps({"policy": "on"})))
        svc = PolicyService(repository=repo)
        result = svc.check(entity_id="u1", entity_type="staff")
        assert result is True

    def test_blacklist_policy_off_returns_false(self):
        import json
        repo = FakeRepo(policy=_policy_record(json.dumps({"policy": "off"})))
        svc = PolicyService(repository=repo)
        result = svc.check(entity_id="u1", entity_type="staff")
        assert result is False

    def test_compete_user_with_access_returns_true(self):
        repo = FakeRepo(
            policy=None,
            user=_user_record("ACCESS"),
        )
        svc = PolicyService(repository=repo)
        # _try_compete won't run because compete user found
        result = svc.check(entity_id="u1", entity_type="staff")
        assert result is True

    def test_compete_user_with_refuse_status_falls_through_to_compete(self):
        """REFUSE status does NOT grant access directly — falls through to _try_compete."""
        repo = FakeRepo(
            policy=None,
            user=_user_record("REFUSE"),
            # no quota -> _try_compete returns False
        )
        svc = PolicyService(repository=repo)
        with patch("agentclaw.community.core.access.services.policy_service.get_current_env", return_value="dev"):
            result = svc.check(entity_id="u1", entity_type="staff")
        assert result is False

    def test_no_policy_no_user_enters_compete_fails(self):
        repo = FakeRepo(policy=None, user=None)
        svc = PolicyService(repository=repo)
        with patch("agentclaw.community.core.access.services.policy_service.get_current_env", return_value="dev"):
            result = svc.check(entity_id="u1", entity_type="staff")
        assert result is False

    def test_no_policy_no_user_enters_compete_succeeds(self):
        repo = FakeRepo(
            policy=None,
            user=None,
            quota=_config("daily_container_quota", "10"),
            update_time=_config("daily_container_update_time", "09:00"),
            compete_count=0,
        )
        svc = PolicyService(repository=repo)
        with patch("agentclaw.community.core.access.services.policy_service.get_current_env", return_value="dev"):
            result = svc.check(entity_id="u1", entity_type="staff")
        assert result is True
        # upsert_user_info should have been called
        assert repo.upserted_user is not None


class TestTryCompete:
    def test_missing_update_time_record_returns_false(self):
        """Lines 89-90: missing update_time_record should return False."""
        repo = FakeRepo(
            quota=_config("daily_container_quota", "10"),
            update_time=None,  # <-- missing
        )
        svc = PolicyService(repository=repo)
        with patch("agentclaw.community.core.access.services.policy_service.get_current_env", return_value="dev"):
            result = svc._try_compete(entity_id="u1")
        assert result is False

    def test_quota_exhausted_returns_false(self):
        repo = FakeRepo(
            quota=_config("daily_container_quota", "5"),
            update_time=_config("daily_container_update_time", "09:00"),
            compete_count=5,  # == quota
        )
        svc = PolicyService(repository=repo)
        with patch("agentclaw.community.core.access.services.policy_service.get_current_env", return_value="dev"):
            result = svc._try_compete(entity_id="u1")
        assert result is False

    def test_quota_available_upserts_and_returns_true(self):
        repo = FakeRepo(
            quota=_config("daily_container_quota", "5"),
            update_time=_config("daily_container_update_time", "09:00"),
            compete_count=3,
        )
        svc = PolicyService(repository=repo)
        with patch("agentclaw.community.core.access.services.policy_service.get_current_env", return_value="dev"):
            result = svc._try_compete(entity_id="u1")
        assert result is True
        assert repo.upserted_user == ("u1", "COMPETE", "ACCESS")

    def test_effective_quota_zero_returns_false(self):
        repo = FakeRepo(quota=None)  # no quota record -> _get_effective_quota returns 0
        svc = PolicyService(repository=repo)
        with patch("agentclaw.community.core.access.services.policy_service.get_current_env", return_value="dev"):
            result = svc._try_compete(entity_id="u1")
        assert result is False
