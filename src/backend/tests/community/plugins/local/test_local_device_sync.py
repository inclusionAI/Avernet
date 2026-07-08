"""Tests for local DeviceSyncPlugin implementations."""

from agentclaw.community.plugins.local.device_sync import (
    LocalDeviceSyncPlugin,
)


def test_sync_bot_config_returns_local_skip_sentinel():
    """LocalDeviceSyncPlugin.sync_bot_config always returns the
    ``local mode — device sync skipped`` sentinel; never touches the
    network or filesystem."""
    plugin = LocalDeviceSyncPlugin(skills_dir=None)
    result = plugin.sync_bot_config(
        bot_id="b1", binding_id=42, public="1",
        permission_owner="owner", user_id="u", nick_name="n",
    )
    assert result == {
        "success": False,
        "message": "local mode — device sync skipped",
    }


def test_sync_bot_config_noop_even_with_zero_binding_id():
    """Sentinel returned regardless of inputs — caller treats local
    mode uniformly."""
    plugin = LocalDeviceSyncPlugin(skills_dir=None)
    result = plugin.sync_bot_config(
        bot_id="b1", binding_id=0, public="0",
        permission_owner=None, user_id="u", nick_name="n",
    )
    assert result["success"] is False
    assert "local mode" in result["message"]



