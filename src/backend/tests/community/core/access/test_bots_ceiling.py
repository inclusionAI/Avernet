"""Tests for PolicyService.get_bots_ceiling / set_bots_ceiling / allow-disallow merge."""
import json
import pytest
from unittest.mock import MagicMock

from agentclaw.community.core.access.services.policy_service import PolicyService
from agentclaw.community.core.access.models import AccessControlPolicyRecord


def _record(entity_id: str = "u1", policy: str | None = None) -> AccessControlPolicyRecord:
    return AccessControlPolicyRecord(id=1, entity_id=entity_id, entity_type="staff", policy=policy)


def _make_service(get_result: AccessControlPolicyRecord | None = None) -> PolicyService:
    repo = MagicMock()
    repo.get_by_entity.return_value = get_result
    svc = PolicyService.__new__(PolicyService)
    svc._repo = repo
    return svc


class TestGetBotsCeiling:
    def test_no_record_returns_default(self):
        svc = _make_service(get_result=None)
        assert svc.get_bots_ceiling(entity_id="u1") == 5

    def test_null_policy_returns_default(self):
        svc = _make_service(get_result=_record(policy=None))
        assert svc.get_bots_ceiling(entity_id="u1") == 5

    def test_no_bots_ceiling_key_returns_default(self):
        svc = _make_service(get_result=_record(policy=json.dumps({"policy": "on"})))
        assert svc.get_bots_ceiling(entity_id="u1") == 5

    def test_valid_bots_ceiling(self):
        svc = _make_service(get_result=_record(policy=json.dumps({"policy": "on", "bots_ceiling": "8"})))
        assert svc.get_bots_ceiling(entity_id="u1") == 8

    def test_custom_default(self):
        svc = _make_service(get_result=None)
        assert svc.get_bots_ceiling(entity_id="u1", default=3) == 3

    def test_zero_bots_ceiling_returns_default(self):
        svc = _make_service(get_result=_record(policy=json.dumps({"bots_ceiling": "0"})))
        assert svc.get_bots_ceiling(entity_id="u1") == 5

    def test_negative_returns_default(self):
        svc = _make_service(get_result=_record(policy=json.dumps({"bots_ceiling": "-1"})))
        assert svc.get_bots_ceiling(entity_id="u1") == 5

    def test_non_numeric_returns_default(self):
        svc = _make_service(get_result=_record(policy=json.dumps({"bots_ceiling": "abc"})))
        assert svc.get_bots_ceiling(entity_id="u1") == 5

    def test_invalid_json_returns_default(self):
        svc = _make_service(get_result=_record(policy="not json"))
        assert svc.get_bots_ceiling(entity_id="u1") == 5


class TestSetBotsCeiling:
    def test_creates_new_record(self):
        svc = _make_service(get_result=None)
        svc.set_bots_ceiling(entity_id="u1", ceiling=10)
        policy_json = json.loads(svc._repo.upsert_policy.call_args.kwargs["policy"])
        assert policy_json["bots_ceiling"] == "10"

    def test_merges_with_existing(self):
        svc = _make_service(get_result=_record(policy=json.dumps({"policy": "on"})))
        svc.set_bots_ceiling(entity_id="u1", ceiling=8)
        policy_json = json.loads(svc._repo.upsert_policy.call_args.kwargs["policy"])
        assert policy_json["policy"] == "on"
        assert policy_json["bots_ceiling"] == "8"

    def test_preserves_other_keys(self):
        svc = _make_service(get_result=_record(policy=json.dumps({"policy": "on", "bots_ceiling": "5"})))
        svc.set_bots_ceiling(entity_id="u1", ceiling=12)
        policy_json = json.loads(svc._repo.upsert_policy.call_args.kwargs["policy"])
        assert policy_json["policy"] == "on"
        assert policy_json["bots_ceiling"] == "12"


class TestAllowDisallowMerge:
    """allow()/disallow() 改为 merge 模式后应保留 bots_ceiling 等其他 key。"""

    def test_allow_preserves_bots_ceiling(self):
        svc = _make_service(get_result=_record(policy=json.dumps({"policy": "off", "bots_ceiling": "10"})))
        svc.allow(entity_id="u1", entity_type="staff")
        policy_json = json.loads(svc._repo.upsert_policy.call_args.kwargs["policy"])
        assert policy_json["policy"] == "on"
        assert policy_json["bots_ceiling"] == "10"

    def test_disallow_preserves_bots_ceiling(self):
        svc = _make_service(get_result=_record(policy=json.dumps({"policy": "on", "bots_ceiling": "8"})))
        svc.disallow(entity_id="u1", entity_type="staff")
        policy_json = json.loads(svc._repo.upsert_policy.call_args.kwargs["policy"])
        assert policy_json["policy"] == "off"
        assert policy_json["bots_ceiling"] == "8"

    def test_allow_no_existing_record_creates_policy_on(self):
        svc = _make_service(get_result=None)
        svc.allow(entity_id="u1", entity_type="staff")
        policy_json = json.loads(svc._repo.upsert_policy.call_args.kwargs["policy"])
        assert policy_json["policy"] == "on"

    def test_disallow_no_existing_record_creates_policy_off(self):
        svc = _make_service(get_result=None)
        svc.disallow(entity_id="u1", entity_type="staff")
        policy_json = json.loads(svc._repo.upsert_policy.call_args.kwargs["policy"])
        assert policy_json["policy"] == "off"