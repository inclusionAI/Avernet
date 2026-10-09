"""Tests for LocalPolicyService — singlebox 全开放 PolicyService 实现。

覆盖 7 个公开方法(check / allow / disallow / get_bots_ceiling /
set_bots_ceiling / clear_bots_ceiling / get_quota)的快乐路径 + structural
Protocol conformance。
"""
from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.plugins.local.policy_service import LocalPolicyService


def test_structural_conforms_to_protocol():
    """LocalPolicyService 不显式继承 Protocol,但 isinstance 检查应通过。

    这是 plugins 层不 import api 层(arch test 强制)的代价 ——
    用 structural typing (@runtime_checkable Protocol + duck typing)
    维持契约。
    """
    svc = LocalPolicyService()
    assert isinstance(svc, PolicyServiceProtocol)


def test_check_always_returns_true():
    svc = LocalPolicyService()
    assert svc.check(entity_id="any-bot", entity_type="bot") is True
    assert svc.check(entity_id="another", entity_type="user") is True


def test_allow_is_noop():
    """allow 不应抛错,也不返回值。"""
    svc = LocalPolicyService()
    assert svc.allow(entity_id="bot-1", entity_type="bot") is None


def test_disallow_is_noop():
    svc = LocalPolicyService()
    assert svc.disallow(entity_id="bot-1", entity_type="bot") is None


def test_get_bots_ceiling_returns_high_limit():
    """singlebox 不限 bot 数。"""
    svc = LocalPolicyService()
    assert svc.get_bots_ceiling(entity_id="user-100014") == 9999
    # default 参数被忽略,总返 9999
    assert svc.get_bots_ceiling(entity_id="any", default=3) == 9999


def test_set_bots_ceiling_is_noop():
    svc = LocalPolicyService()
    assert svc.set_bots_ceiling(entity_id="user-1", ceiling=10) is None


def test_clear_bots_ceiling_is_noop():
    svc = LocalPolicyService()
    assert svc.clear_bots_ceiling(entity_id="user-1") is False


def test_get_quota_returns_high_limits():
    svc = LocalPolicyService()
    q = svc.get_quota()
    assert q == {
        "quota": 9999,
        "totalLimit": 9999,
        "activeCount": 0,
        "effectiveQuota": 9999,
        "updateTime": "00:00",
    }
