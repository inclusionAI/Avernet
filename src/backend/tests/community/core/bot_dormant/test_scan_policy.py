from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_dormant.scan_policy import (
    DormantScanPolicyService,
    positive_int_or_default,
    resolve_scan_window,
)
from agentclaw.community.core.bot_dormant.service import DormantBotService


def _svc(value=None, *, enable="1"):
    common_config = MagicMock()
    if value is None and enable is None:
        common_config.get_config.return_value = None
    else:
        common_config.get_config.return_value = {
            "enable": enable,
            "param_value": value,
        }
    return DormantScanPolicyService(common_config)


@pytest.mark.unit
def test_missing_policy_falls_back_to_prod_dry_run_enabled():
    svc = _svc(enable=None)
    with patch("agentclaw.community.core.bot_dormant.scan_policy.get_current_env", return_value="prod"):
        policy = svc.get_policy()
    assert policy.scheduled_scan_enabled is True
    assert policy.dry_run is True
    assert policy.inactive_threshold_days == 7
    assert policy.recycle_grace_days == 3
    assert policy.source == "fallback_missing"


@pytest.mark.unit
def test_missing_policy_falls_back_to_pre_disabled_dry_run():
    svc = _svc(enable=None)
    with patch("agentclaw.community.core.bot_dormant.scan_policy.get_current_env", return_value="pre"):
        policy = svc.get_policy()
    assert policy.scheduled_scan_enabled is False
    assert policy.dry_run is True
    assert policy.source == "fallback_missing"


@pytest.mark.unit
def test_disabled_policy_row_disables_scan():
    svc = _svc({"scheduled_scan_enabled": True, "dry_run": False}, enable="0")
    with patch("agentclaw.community.core.bot_dormant.scan_policy.get_current_env", return_value="prod"):
        policy = svc.get_policy()
    assert policy.scheduled_scan_enabled is False
    assert policy.dry_run is True
    assert policy.source == "common_config_disabled"


@pytest.mark.unit
def test_enabled_policy_row_controls_scan_and_dry_run():
    svc = _svc({"scheduled_scan_enabled": True, "dry_run": False})
    with patch("agentclaw.community.core.bot_dormant.scan_policy.get_current_env", return_value="pre"):
        policy = svc.get_policy()
    assert policy.scheduled_scan_enabled is True
    assert policy.dry_run is False
    assert policy.inactive_threshold_days == 7
    assert policy.recycle_grace_days == 3
    assert policy.source == "common_config"


@pytest.mark.unit
def test_enabled_policy_row_controls_scan_thresholds():
    svc = _svc({
        "scheduled_scan_enabled": True,
        "dry_run": True,
        "inactive_threshold_days": "14",
        "recycle_grace_days": 5,
    })
    with patch("agentclaw.community.core.bot_dormant.scan_policy.get_current_env", return_value="prod"):
        policy = svc.get_policy()
    assert policy.inactive_threshold_days == 14
    assert policy.recycle_grace_days == 5


@pytest.mark.unit
def test_invalid_policy_thresholds_fall_back_to_defaults():
    svc = _svc({
        "scheduled_scan_enabled": True,
        "dry_run": True,
        "inactive_threshold_days": object(),
        "recycle_grace_days": "bad",
    })
    with patch("agentclaw.community.core.bot_dormant.scan_policy.get_current_env", return_value="prod"):
        policy = svc.get_policy()
    assert policy.inactive_threshold_days == 7
    assert policy.recycle_grace_days == 3


@pytest.mark.unit
def test_invalid_policy_value_uses_fallback():
    svc = _svc("not-a-dict")
    with patch("agentclaw.community.core.bot_dormant.scan_policy.get_current_env", return_value="pre"):
        policy = svc.get_policy()
    assert policy.scheduled_scan_enabled is False
    assert policy.dry_run is True
    assert policy.source == "fallback_invalid"


@pytest.mark.unit
def test_policy_read_error_uses_safe_fallback():
    common_config = MagicMock()
    common_config.get_config.side_effect = RuntimeError("db unavailable")
    svc = DormantScanPolicyService(common_config)

    with patch("agentclaw.community.core.bot_dormant.scan_policy.get_current_env", return_value="prod"):
        policy = svc.get_policy()

    assert policy.scheduled_scan_enabled is True
    assert policy.dry_run is True
    assert policy.source == "fallback_error"


@pytest.mark.unit
def test_policy_bool_parser_accepts_numbers_strings_and_unknown_values():
    svc = _svc({"scheduled_scan_enabled": 1, "dry_run": "false"})
    with patch("agentclaw.community.core.bot_dormant.scan_policy.get_current_env", return_value="pre"):
        policy = svc.get_policy()
    assert policy.scheduled_scan_enabled is True
    assert policy.dry_run is False

    svc = _svc({"scheduled_scan_enabled": [], "dry_run": []})
    with patch("agentclaw.community.core.bot_dormant.scan_policy.get_current_env", return_value="prod"):
        policy = svc.get_policy()
    assert policy.scheduled_scan_enabled is True
    assert policy.dry_run is True


@pytest.mark.unit
def test_policy_accessor_methods_return_effective_values():
    svc = _svc({
        "scheduled_scan_enabled": False,
        "dry_run": False,
        "inactive_threshold_days": 9,
        "recycle_grace_days": 2,
    })
    with patch("agentclaw.community.core.bot_dormant.scan_policy.get_current_env", return_value="prod"):
        assert svc.scheduled_scan_enabled() is False
        assert svc.dry_run() is False
        assert svc.inactive_threshold_days() == 9
        assert svc.recycle_grace_days() == 2


@pytest.mark.unit
def test_dormant_bot_service_reads_dry_run_from_scan_policy():
    scan_policy = MagicMock()
    scan_policy.dry_run.return_value = False
    service = DormantBotService(
        db=MagicMock(),
        baas_client=MagicMock(),
        bot_service=MagicMock(),
        passport_plugin=MagicMock(),
        scan_policy=scan_policy,
    )

    assert service.is_dry_run() is False
    scan_policy.dry_run.assert_called_once_with()


@pytest.mark.unit
def test_resolve_scan_window_uses_policy_values():
    scan_policy = MagicMock()
    scan_policy.get_policy.return_value.inactive_threshold_days = 11
    scan_policy.get_policy.return_value.recycle_grace_days = 4

    assert resolve_scan_window(
        scan_policy,
        default_inactive_threshold_days=7,
        default_recycle_grace_days=3,
    ) == (11, 4)


@pytest.mark.unit
def test_dormant_bot_service_scan_window_parser_falls_back_for_invalid_values():
    assert positive_int_or_default(None, 7) == 7
    assert positive_int_or_default(False, 7) == 7
    assert positive_int_or_default(object(), 7) == 7
    assert positive_int_or_default("bad", 7) == 7
    assert positive_int_or_default(0, 7) == 7


@pytest.mark.unit
def test_resolve_scan_window_falls_back_on_policy_error():
    scan_policy = MagicMock()
    scan_policy.get_policy.side_effect = RuntimeError("db unavailable")

    assert resolve_scan_window(
        scan_policy,
        default_inactive_threshold_days=10,
        default_recycle_grace_days=6,
    ) == (10, 6)
