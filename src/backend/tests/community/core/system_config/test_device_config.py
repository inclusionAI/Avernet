"""Tests for agentclaw.community.core.system_config.device_config.DeviceConfigService.

Focus: ``get_allocation_provider`` routing precedence, plus the new template_type
gating / baas_users whitelist / personal_bot_baas_disable kill-switch introduced
for the personal-bot-via-BaaS rollout.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.system_config.device_config import DeviceConfigService


def _make_service(config_map: dict | None = None) -> tuple[DeviceConfigService, MagicMock]:
    """Build a service backed by a MagicMock SystemConfigService.

    ``config_map`` keys: tuple of (category, config_key) → value.
    Anything not in the map returns ``None`` (the production behaviour
    when a key has never been written).
    """
    cfg_map = config_map or {}
    mock_config = MagicMock()

    def fake_get_config(*, category: str, config_key: str, env: str):
        return cfg_map.get((category, config_key))

    mock_config.get_config.side_effect = fake_get_config
    svc = DeviceConfigService(config_service=mock_config)
    return svc, mock_config


class TestGetAllocationProvider:
    """Routing precedence (from highest to lowest):

    1. personal_bot_baas_disable + bot_type=personal → arca
    2. template_type → provider map
    3. arca_users whitelist
    4. baas_users whitelist
    5. default_provider (fallback arca)
    """

    def test_emergency_kill_switch_forces_arca_for_personal(self):
        svc, _ = _make_service({
            ("device", "personal_bot_baas_disable"): True,
            # would otherwise be routed to baas via template_type map
            ("device", "template_type_provider_map"): {"personalCoding": "baas"},
        })

        result = svc.get_allocation_provider(
            staff_id="u001", env="pre",
            template_type="personalCoding", bot_type="personal",
        )
        assert result == "arca"

    def test_kill_switch_ignored_for_non_personal_bots(self):
        """The kill-switch is scoped to ``bot_type=personal`` so service / desktop
        bots are unaffected even if it is on."""
        svc, _ = _make_service({
            ("device", "personal_bot_baas_disable"): True,
            ("device", "baas_users"): ["u001"],
        })

        result = svc.get_allocation_provider(
            staff_id="u001", env="pre",
            template_type=None, bot_type="service",
        )
        # baas_users whitelist still wins because the kill-switch did not apply
        assert result == "baas"

    def test_template_type_map_hits_baas(self):
        svc, _ = _make_service({
            ("device", "template_type_provider_map"): {"personalCoding": "baas"},
        })

        result = svc.get_allocation_provider(
            staff_id="u999", env="pre",
            template_type="personalCoding", bot_type="personal",
        )
        assert result == "baas"

    def test_template_type_map_invalid_provider_ignored(self):
        """A typo or stale value in the map should not crash — fall through to
        the next rule instead."""
        svc, _ = _make_service({
            ("device", "template_type_provider_map"): {"personalCoding": "mystery"},
            ("device", "default_provider"): "arca",
        })

        result = svc.get_allocation_provider(
            staff_id="u999", env="pre",
            template_type="personalCoding", bot_type="personal",
        )
        assert result == "arca"

    def test_template_type_not_in_map_falls_through(self):
        svc, _ = _make_service({
            ("device", "template_type_provider_map"): {"personalCoding": "baas"},
            ("device", "arca_users"): ["u001"],
        })

        result = svc.get_allocation_provider(
            staff_id="u001", env="pre",
            template_type="applicationCoding",  # not in map
            bot_type="personal",
        )
        # falls through to arca_users whitelist
        assert result == "arca"

    def test_arca_whitelist_wins_over_default(self):
        svc, _ = _make_service({
            ("device", "arca_users"): ["u001"],
            ("device", "default_provider"): "baas",
        })

        result = svc.get_allocation_provider(staff_id="u001", env="pre")
        assert result == "arca"

    def test_baas_whitelist(self):
        svc, _ = _make_service({
            ("device", "baas_users"): ["u002"],
        })

        result = svc.get_allocation_provider(staff_id="u002", env="pre")
        assert result == "baas"

    def test_arca_takes_precedence_over_baas_when_user_in_both(self):
        """A user accidentally in both lists must not flap — arca wins because
        it is checked first."""
        svc, _ = _make_service({
            ("device", "arca_users"): ["u003"],
            ("device", "baas_users"): ["u003"],
        })

        result = svc.get_allocation_provider(staff_id="u003", env="pre")
        assert result == "arca"

    def test_default_provider_fallback(self):
        svc, _ = _make_service({
            ("device", "default_provider"): "arca",
        })

        result = svc.get_allocation_provider(staff_id="u999", env="pre")
        assert result == "arca"

    def test_default_provider_invalid_falls_back_to_arca(self):
        """Legacy 'daas' or any other invalid value must not leak through."""
        svc, _ = _make_service({
            ("device", "default_provider"): "daas",
        })

        result = svc.get_allocation_provider(staff_id="u999", env="pre")
        assert result == "arca"

    def test_empty_config_returns_arca(self):
        """No config at all → fall through to hard-coded arca default."""
        svc, _ = _make_service()
        result = svc.get_allocation_provider(staff_id="u999", env="pre")
        assert result == "arca"


class TestProviderValidation:
    def test_add_to_allocation_list_rejects_unknown_provider(self):
        svc, _ = _make_service()
        with pytest.raises(ValueError):
            svc.add_to_allocation_list(staff_ids=["u001"], provider="daas", env="pre")

    def test_add_to_allocation_list_accepts_baas(self):
        svc, mock_config = _make_service()
        # No existing list → returns []; add 1 staff → 1 added
        added = svc.add_to_allocation_list(staff_ids=["u001"], provider="baas", env="pre")
        assert added == 1
        # Verify it wrote to the baas_users key, not daas_users
        mock_config.set_config.assert_called_once()
        kwargs = mock_config.set_config.call_args.kwargs
        assert kwargs["config_key"] == "baas_users"
        assert "u001" in kwargs["config_value"]

    def test_set_default_provider_rejects_daas(self):
        svc, _ = _make_service()
        with pytest.raises(ValueError):
            svc.set_default_provider(provider="daas", env="pre")

    def test_set_template_type_provider_map_validates_values(self):
        svc, _ = _make_service()
        with pytest.raises(ValueError):
            svc.set_template_type_provider_map(
                mapping={"personalCoding": "mystery"}, env="pre",
            )

    def test_set_template_type_provider_map_accepts_valid_mapping(self):
        svc, mock_config = _make_service()
        svc.set_template_type_provider_map(
            mapping={"personalCoding": "baas", "applicationCoding": "baas"},
            env="pre",
        )
        mock_config.set_config.assert_called_once()
        kwargs = mock_config.set_config.call_args.kwargs
        assert kwargs["config_key"] == "template_type_provider_map"
        assert kwargs["config_value"]["personalCoding"] == "baas"


class TestGetAllocationLists:
    def test_returns_all_keys(self):
        svc, _ = _make_service({
            ("device", "arca_users"): ["u001"],
            ("device", "baas_users"): ["u002"],
            ("device", "template_type_provider_map"): {"personalCoding": "baas"},
            ("device", "personal_bot_baas_disable"): True,
            ("device", "default_provider"): "arca",
        })

        result = svc.get_allocation_lists(env="pre")
        assert result == {
            "arca_users": ["u001"],
            "baas_users": ["u002"],
            "template_type_provider_map": {"personalCoding": "baas"},
            "personal_bot_baas_disable": True,
            "default_provider": "arca",
        }

    def test_returns_safe_defaults_when_empty(self):
        svc, _ = _make_service()
        result = svc.get_allocation_lists(env="pre")
        assert result == {
            "arca_users": [],
            "baas_users": [],
            "template_type_provider_map": {},
            "personal_bot_baas_disable": False,
            "default_provider": "arca",
        }



class TestRemoveFromAllocationList:
    """Cover remove_from_allocation_list error + baas path."""

    def test_rejects_unknown_provider(self):
        svc, _ = _make_service()
        with pytest.raises(ValueError, match="Invalid provider"):
            svc.remove_from_allocation_list(provider="daas", staff_ids=["u1"], env="dev")

    def test_removes_from_baas_list(self):
        svc, mock_cfg = _make_service({
            ("device", "baas_users"): ["u1", "u2"],
        })
        svc.remove_from_allocation_list(provider="baas", staff_ids=["u1"], env="dev")
        mock_cfg.set_config.assert_called_once()
        call_kw = mock_cfg.set_config.call_args[1]
        assert call_kw["config_key"] == "baas_users"
        assert "u1" not in call_kw["config_value"]


class TestSetPersonalBotBaasDisable:
    """Cover set_personal_bot_baas_disable."""

    def test_calls_set_config_with_correct_params(self):
        svc, mock_cfg = _make_service()
        svc.set_personal_bot_baas_disable(disabled=True, env="dev", creator="admin")
        mock_cfg.set_config.assert_called_once()
        call_kw = mock_cfg.set_config.call_args[1]
        assert call_kw["config_key"] == "personal_bot_baas_disable"
        assert call_kw["config_value"] is True
        assert call_kw["operator"] == "admin"

    def test_disable_forces_arca_for_personal_bot(self):
        svc, _ = _make_service({
            ("device", "personal_bot_baas_disable"): True,
            ("device", "default_provider"): "baas",
        })
        result = svc.get_allocation_provider(staff_id="u1", env="dev", bot_type="personal")
        assert result == "arca"
