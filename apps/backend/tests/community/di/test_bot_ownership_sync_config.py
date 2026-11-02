"""Ownership is a strict, default-off extension of the shared sync config."""
import pytest

from agentclaw.community.di.bot_manager_sync_config import (
    ManagerSyncConfig,
    parse_manager_sync_config,
)

_TARGET = {"env": "dev", "bcs_base_url": "http://bcs.example.test"}


@pytest.mark.parametrize("block", [None, {}, {"enabled": True, **_TARGET}])
def test_old_config_keeps_ownership_off(block):
    assert parse_manager_sync_config(block).ownership_enabled is False


@pytest.mark.parametrize("enabled,ownership", [(False, False), (True, False), (True, True)])
def test_valid_switch_states(enabled, ownership):
    config = parse_manager_sync_config({
        "enabled": enabled, "ownership_enabled": ownership, **_TARGET,
    })
    assert config.enabled is enabled
    assert config.ownership_enabled is ownership


@pytest.mark.parametrize("enabled", [None, False])
def test_owner_only_is_rejected(enabled):
    block = {"ownership_enabled": True, **_TARGET}
    if enabled is not None:
        block["enabled"] = enabled
    with pytest.raises(ValueError, match="requires.*enabled"):
        parse_manager_sync_config(block)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("value", [None, 0, 1, "false", "true", [], {}])
def test_ownership_boolean_is_strict_even_when_disabled(enabled, value):
    with pytest.raises(ValueError, match="ownership_enabled.*boolean"):
        parse_manager_sync_config({
            "enabled": enabled, "ownership_enabled": value, **_TARGET,
        })


@pytest.mark.parametrize("field", ["env", "bcs_base_url"])
def test_enabled_ownership_keeps_required_target_validation(field):
    block = {"enabled": True, "ownership_enabled": True, **_TARGET}
    del block[field]
    with pytest.raises(ValueError, match=field):
        parse_manager_sync_config(block)


def test_ownership_keeps_unknown_key_and_origin_validation():
    with pytest.raises(ValueError, match="unknown"):
        parse_manager_sync_config({"ownership_enabled": False, "owner_endpoint": "x"})
    with pytest.raises(ValueError, match="origin"):
        parse_manager_sync_config({
            "enabled": True, "ownership_enabled": True,
            "env": "dev", "bcs_base_url": "http://bcs.example.test/path",
        })
    assert ManagerSyncConfig().ownership_enabled is False
