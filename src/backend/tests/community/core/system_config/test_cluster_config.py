"""Unit tests for ClusterConfigService — alt-tenant traffic / gray / whitelist.

The service is thin logic over SystemConfigService, so a mocked config service
exercises every branch (default fallbacks, validation, format guards, dedup).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.system_config.cluster_config import ClusterConfigService


def _svc():
    config = MagicMock()
    return ClusterConfigService(config), config


# ── alt_traffic ───────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [(1, 1), ("1", 1), (0, 0)])
def test_get_alt_traffic_parses_int(raw, expected) -> None:
    svc, config = _svc()
    config.get_config.return_value = raw
    assert svc.get_alt_traffic(env="dev") == expected


@pytest.mark.unit
def test_get_alt_traffic_default_when_missing() -> None:
    svc, config = _svc()
    config.get_config.return_value = None
    assert svc.get_alt_traffic(env="dev") == 0


@pytest.mark.unit
def test_get_alt_traffic_default_when_invalid() -> None:
    svc, config = _svc()
    config.get_config.return_value = "not-an-int"
    assert svc.get_alt_traffic(env="dev") == 0


@pytest.mark.unit
def test_set_alt_traffic_persists_valid_value() -> None:
    svc, config = _svc()
    svc.set_alt_traffic(value=1, env="dev", creator="op")
    assert config.set_config.call_args.kwargs["config_value"] == 1
    assert config.set_config.call_args.kwargs["config_key"] == "alt_traffic"


@pytest.mark.unit
def test_set_alt_traffic_rejects_out_of_range() -> None:
    svc, config = _svc()
    with pytest.raises(ValueError, match="alt_traffic"):
        svc.set_alt_traffic(value=2, env="dev")
    config.set_config.assert_not_called()


# ── alt_gray_users ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_get_alt_gray_users_valid_dict() -> None:
    svc, config = _svc()
    config.get_config.return_value = {"target_alt": 1, "gray_users": ["u1"]}
    assert svc.get_alt_gray_users(env="dev") == {"target_alt": 1, "gray_users": ["u1"]}


@pytest.mark.unit
def test_get_alt_gray_users_none_when_missing() -> None:
    svc, config = _svc()
    config.get_config.return_value = None
    assert svc.get_alt_gray_users(env="dev") is None


@pytest.mark.unit
def test_get_alt_gray_users_none_when_malformed() -> None:
    svc, config = _svc()
    config.get_config.return_value = {"target_alt": 1}  # missing gray_users
    assert svc.get_alt_gray_users(env="dev") is None


@pytest.mark.unit
def test_set_alt_gray_users_dedups_and_persists() -> None:
    svc, config = _svc()
    svc.set_alt_gray_users(target_alt=1, gray_users=["a", "a", "b"], env="dev")
    stored = config.set_config.call_args.kwargs["config_value"]
    assert stored["target_alt"] == 1
    assert sorted(stored["gray_users"]) == ["a", "b"]


@pytest.mark.unit
def test_set_alt_gray_users_rejects_bad_target() -> None:
    svc, config = _svc()
    with pytest.raises(ValueError, match="target_alt"):
        svc.set_alt_gray_users(target_alt=9, gray_users=[], env="dev")
    config.set_config.assert_not_called()


@pytest.mark.unit
def test_is_gray_user() -> None:
    svc, config = _svc()
    config.get_config.return_value = {"target_alt": 1, "gray_users": ["u1", "u2"]}
    assert svc.is_gray_user(staff_id="u2", env="dev") is True
    assert svc.is_gray_user(staff_id="other", env="dev") is False


@pytest.mark.unit
def test_is_gray_user_false_when_no_config() -> None:
    svc, config = _svc()
    config.get_config.return_value = None
    assert svc.is_gray_user(staff_id="u1", env="dev") is False


# ── template whitelist ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_get_template_whitelist_valid_none_malformed() -> None:
    svc, config = _svc()
    config.get_config.return_value = {"template_id": "T", "staff_ids": ["u1"]}
    assert svc.get_template_whitelist(env="dev") == {"template_id": "T", "staff_ids": ["u1"]}
    config.get_config.return_value = None
    assert svc.get_template_whitelist(env="dev") is None
    config.get_config.return_value = {"template_id": "T"}  # missing staff_ids
    assert svc.get_template_whitelist(env="dev") is None


@pytest.mark.unit
def test_set_template_whitelist_dedups_and_persists() -> None:
    svc, config = _svc()
    svc.set_template_whitelist(template_id="T", staff_ids=["a", "a"], env="dev")
    stored = config.set_config.call_args.kwargs["config_value"]
    assert stored["template_id"] == "T"
    assert stored["staff_ids"] == ["a"]


@pytest.mark.unit
def test_is_template_whitelist_user_hit() -> None:
    svc, config = _svc()
    config.get_config.return_value = {"template_id": "T7", "staff_ids": ["u1"]}
    assert svc.is_template_whitelist_user(staff_id="u1", env="dev") == (True, "T7")


@pytest.mark.unit
def test_is_template_whitelist_user_miss_and_unconfigured() -> None:
    svc, config = _svc()
    config.get_config.return_value = {"template_id": "T7", "staff_ids": ["u1"]}
    assert svc.is_template_whitelist_user(staff_id="other", env="dev") == (False, None)
    config.get_config.return_value = None
    assert svc.is_template_whitelist_user(staff_id="u1", env="dev") == (False, None)


# ── use_aicoding_tenant switch ─────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [(True, True), ("true", True), (False, False), (None, False)])
def test_get_use_aicoding_tenant(raw, expected):
    svc, config = _svc()
    config.get_config.return_value = raw
    assert svc.get_use_aicoding_tenant(env="dev") is expected


@pytest.mark.unit
def test_set_use_aicoding_tenant_writes_bool():
    svc, config = _svc()
    svc.set_use_aicoding_tenant(enabled=True, env="dev")
    assert config.set_config.call_args.kwargs["config_key"] == "use_aicoding_tenant"
    assert config.set_config.call_args.kwargs["config_value"] is True
