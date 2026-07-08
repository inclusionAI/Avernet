"""
Tests for TomlUtils with Moltis config file (complex nested tables with comments).
Uses real fixture file: tests/utils/moltis_config.toml (same directory as this test file)
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from agentclaw.community.utils.toml_utils import TomlFile, TomlUtils


# Get path to real fixture file (same directory as this test file)
TEST_DIR = Path(__file__).parent
MOLTIS_CONFIG_FILE = TEST_DIR / "moltis_config.toml"

# The new channel config to add
NEW_CHANNEL_CONFIG = {
    "client_id": "dingl6x1pu7l5y9itkut",
    "client_secret": "cXi3cLfK17vT4i4z-j08qdEy_Pb8Gj-9ZJwl61jpsJHl7oVNLb9MAv4wiMPgoHYF",
    "dm_policy": "open",
    "allowlist": ["*"],
    "reply_to_message": True,
    "robot_code": "dingl6x1pu7l5y9itkut",
    "card_template_id": "4d98ff02-25be-437b-933d-9884bac9acca.schema",
    "card_template_key": "content",
    "enable_streaming_cards": True,
    "aix_enable": True,
    "aix_preview_url": "http://local.teamclaw.net:8001/preview",
    "include_sender_name": True,
}


@pytest.fixture
def moltis_config_file():
    """Create a temporary copy of real Moltis config file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        # Copy content from real fixture file
        real_config = MOLTIS_CONFIG_FILE.read_text()
        f.write(real_config)
        f.flush()
        temp_path = Path(f.name)

    yield temp_path

    # Cleanup
    temp_path.unlink(missing_ok=True)


class TestRealMoltisFile:
    """Test with real fixture file (direct load)."""

    def test_load_real_moltis_config(self):
        """Test loading real moltis_config.toml fixture file."""
        # Directly load the real fixture file
        config = TomlUtils.load(MOLTIS_CONFIG_FILE)

        # Verify structure
        assert "server" in config
        assert config["server"]["port"] == 20001
        assert config["server"]["bind"] == "0.0.0.0"
        assert config["server"]["cors_allow_all"] is True

        # Verify channels section exists
        assert "channels" in config
        assert "telegram" in config["channels"]

        # Verify comments are preserved when loading raw
        doc = TomlUtils.load(MOLTIS_CONFIG_FILE, raw=True)
        content = TomlUtils.dumps(doc)
        assert "# Moltis Configuration" in content
        assert "# ====================" in content

    def test_add_channel_to_real_config_and_verify_persistence(self, tmp_path):
        """Test adding channel to copy of real config and verify file content."""
        # Copy real config to temp location
        temp_file = tmp_path / "moltis_test.toml"
        shutil.copy(MOLTIS_CONFIG_FILE, temp_file)

        # Add channel using generic key path
        TomlUtils.set(
            temp_file,
            "channels.dingtalk.semanticflowali",
            NEW_CHANNEL_CONFIG,
        )

        # Verify by reading the actual file content
        content = temp_file.read_text()

        # Original comments preserved
        assert "# Moltis Configuration" in content
        assert "# ====================" in content
        assert "# ══════════════════════════════════════════════════════════════════════════════" in content
        assert "# Server" in content.lower() or "[server]" in content

        # New channel added
        assert "[channels.dingtalk.semanticflowali]" in content
        assert 'client_id = "dingl6x1pu7l5y9itkut"' in content
        assert 'client_secret = "cXi3cLfK17vT4i4z-j08qdEy_Pb8Gj-9ZJwl61jpsJHl7oVNLb9MAv4wiMPgoHYF"' in content
        assert "allowlist = [" in content
        assert '"*"' in content

        # Verify can reload and read
        reloaded = TomlUtils.get(temp_file, "channels.dingtalk.semanticflowali")
        assert reloaded is not None
        assert reloaded["client_id"] == "dingl6x1pu7l5y9itkut"
        assert reloaded["enable_streaming_cards"] is True


class TestMoltisConfigCRUD:
    """Test CRUD operations on Moltis config file."""

    def test_create_channel_config(self, moltis_config_file):
        """Test adding new channel [channels.dingtalk.semanticflowali]."""
        # Add the new channel configuration using generic set
        TomlUtils.set(
            moltis_config_file,
            "channels.dingtalk.semanticflowali",
            NEW_CHANNEL_CONFIG,
        )

        # Verify the channel was added
        channel = TomlUtils.get(moltis_config_file, "channels.dingtalk.semanticflowali")

        assert channel is not None
        assert channel["client_id"] == "dingl6x1pu7l5y9itkut"
        assert channel["dm_policy"] == "open"
        assert channel["allowlist"] == ["*"]
        assert channel["reply_to_message"] is True
        assert channel["aix_enable"] is True
        assert channel["include_sender_name"] is True

    def test_read_channel_config(self, moltis_config_file):
        """Test reading channel configuration with nested access."""
        # First create the channel
        TomlUtils.set(
            moltis_config_file,
            "channels.dingtalk.semanticflowali",
            NEW_CHANNEL_CONFIG,
        )

        # Read individual fields using dot notation
        client_id = TomlUtils.get(
            moltis_config_file, "channels.dingtalk.semanticflowali.client_id"
        )
        assert client_id == "dingl6x1pu7l5y9itkut"

        # Read array field
        allowlist = TomlUtils.get(
            moltis_config_file, "channels.dingtalk.semanticflowali.allowlist"
        )
        assert allowlist == ["*"]

        # Read boolean field
        reply_to_message = TomlUtils.get(
            moltis_config_file, "channels.dingtalk.semanticflowali.reply_to_message"
        )
        assert reply_to_message is True

        # Read entire channel config
        channel = TomlUtils.get(
            moltis_config_file, "channels.dingtalk.semanticflowali"
        )
        assert isinstance(channel, dict)
        assert channel["card_template_id"] == "4d98ff02-25be-437b-933d-9884bac9acca.schema"

    def test_update_channel_config(self, moltis_config_file):
        """Test updating existing channel configuration."""
        # Create first
        TomlUtils.set(
            moltis_config_file,
            "channels.dingtalk.semanticflowali",
            NEW_CHANNEL_CONFIG,
        )

        # Update single field
        TomlUtils.set(
            moltis_config_file,
            "channels.dingtalk.semanticflowali.client_id",
            "new_client_id_123",
        )

        # Update multiple fields using batch_set
        TomlUtils.batch_set(moltis_config_file, {
            "channels.dingtalk.semanticflowali.dm_policy": "disabled",
            "channels.dingtalk.semanticflowali.aix_enable": False,
            "channels.dingtalk.semanticflowali.aix_preview_url": "http://new.url:8080/preview",
        })

        # Verify updates
        client_id = TomlUtils.get(
            moltis_config_file, "channels.dingtalk.semanticflowali.client_id"
        )
        assert client_id == "new_client_id_123"

        dm_policy = TomlUtils.get(
            moltis_config_file, "channels.dingtalk.semanticflowali.dm_policy"
        )
        assert dm_policy == "disabled"

        aix_enable = TomlUtils.get(
            moltis_config_file, "channels.dingtalk.semanticflowali.aix_enable"
        )
        assert aix_enable is False

        # Verify other fields unchanged
        allowlist = TomlUtils.get(
            moltis_config_file, "channels.dingtalk.semanticflowali.allowlist"
        )
        assert allowlist == ["*"]

    def test_delete_channel_config(self, moltis_config_file):
        """Test deleting channel configuration."""
        # Create first
        TomlUtils.set(
            moltis_config_file,
            "channels.dingtalk.semanticflowali",
            NEW_CHANNEL_CONFIG,
        )

        # Add another channel to test isolation
        TomlUtils.set(
            moltis_config_file,
            "channels.dingtalk.another_channel",
            {"client_id": "another_id"},
        )

        # Delete the first channel
        result = TomlUtils.delete(
            moltis_config_file, "channels.dingtalk.semanticflowali"
        )
        assert result is True

        # Verify deleted
        channel = TomlUtils.get(moltis_config_file, "channels.dingtalk.semanticflowali")
        assert channel is None

        # Verify the other channel still exists
        another = TomlUtils.get(moltis_config_file, "channels.dingtalk.another_channel")
        assert another is not None
        assert another["client_id"] == "another_id"

    def test_allowlist_array_operations(self, moltis_config_file):
        """Test adding/removing from allowlist array."""
        # Create channel
        TomlUtils.set(
            moltis_config_file,
            "channels.dingtalk.semanticflowali",
            NEW_CHANNEL_CONFIG,
        )

        # Add specific staff_id to allowlist
        TomlUtils.remove(
            moltis_config_file,
            "channels.dingtalk.semanticflowali.allowlist",
            "*",
        )
        TomlUtils.append(
            moltis_config_file,
            "channels.dingtalk.semanticflowali.allowlist",
            "100017",
        )
        TomlUtils.append(
            moltis_config_file,
            "channels.dingtalk.semanticflowali.allowlist",
            "61257",
        )

        allowlist = TomlUtils.get(
            moltis_config_file, "channels.dingtalk.semanticflowali.allowlist"
        )
        assert "100017" in allowlist
        assert "61257" in allowlist
        assert "*" not in allowlist

        # Remove a staff_id
        TomlUtils.remove(
            moltis_config_file,
            "channels.dingtalk.semanticflowali.allowlist",
            "100017",
        )

        allowlist = TomlUtils.get(
            moltis_config_file, "channels.dingtalk.semanticflowali.allowlist"
        )
        assert "100017" not in allowlist
        assert "61257" in allowlist

    def test_format_preserved(self, moltis_config_file):
        """Test that comments and format are preserved after modifications."""
        # Read original content
        original_content = moltis_config_file.read_text()
        assert "# Moltis Configuration" in original_content
        assert "# ====================" in original_content
        assert "# External messaging integrations." in original_content

        # Add channel
        TomlUtils.set(
            moltis_config_file,
            "channels.dingtalk.semanticflowali",
            NEW_CHANNEL_CONFIG,
        )

        # Read modified content
        modified_content = moltis_config_file.read_text()

        # Verify original comments are preserved
        assert "# Moltis Configuration" in modified_content
        assert "# ====================" in modified_content
        assert "# External messaging integrations." in modified_content
        assert "# ══════════════════════════════════════════════════════════════════════════════" in modified_content

        # Verify new config was added
        assert "[channels.dingtalk.semanticflowali]" in modified_content
        assert 'client_id = "dingl6x1pu7l5y9itkut"' in modified_content

    def test_exists_check(self, moltis_config_file):
        """Test checking if channel exists using generic exists method."""
        # Not exists initially
        exists = TomlUtils.exists(
            moltis_config_file, "channels.dingtalk.semanticflowali"
        )
        assert exists is False

        # Create channel
        TomlUtils.set(
            moltis_config_file,
            "channels.dingtalk.semanticflowali",
            NEW_CHANNEL_CONFIG,
        )

        # Now exists
        exists = TomlUtils.exists(
            moltis_config_file, "channels.dingtalk.semanticflowali"
        )
        assert exists is True

        exists_field = TomlUtils.exists(
            moltis_config_file, "channels.dingtalk.semanticflowali.client_id"
        )
        assert exists_field is True

        not_exists_field = TomlUtils.exists(
            moltis_config_file, "channels.dingtalk.semanticflowali.nonexistent"
        )
        assert not_exists_field is False


class TestMoltisConfigEdgeCases:
    """Test edge cases for Moltis config."""

    def test_batch_update_channel(self, moltis_config_file):
        """Test batch updating multiple fields."""
        TomlUtils.set(
            moltis_config_file,
            "channels.dingtalk.semanticflowali",
            NEW_CHANNEL_CONFIG,
        )

        # Batch update
        TomlUtils.batch_set(moltis_config_file, {
            "channels.dingtalk.semanticflowali.dm_policy": "disabled",
            "channels.dingtalk.semanticflowali.reply_to_message": False,
            "channels.dingtalk.semanticflowali.enable_streaming_cards": False,
        })

        # Verify all updated
        assert TomlUtils.get(moltis_config_file, "channels.dingtalk.semanticflowali.dm_policy") == "disabled"
        assert TomlUtils.get(moltis_config_file, "channels.dingtalk.semanticflowali.reply_to_message") is False
        assert TomlUtils.get(moltis_config_file, "channels.dingtalk.semanticflowali.enable_streaming_cards") is False

    def test_channel_with_special_chars_in_value(self, moltis_config_file):
        """Test channel config with special characters."""
        special_config = {
            "client_id": "ding_xxx_123",
            "client_secret": "secret-with-dashes_and_underscores",
            "card_template_id": "uuid.schema",  # with dot
            "aix_preview_url": "http://local.teamclaw.net:8001/preview",  # URL with port
        }

        TomlUtils.set(
            moltis_config_file,
            "channels.dingtalk.special",
            special_config,
        )

        channel = TomlUtils.get(moltis_config_file, "channels.dingtalk.special")
        assert channel["client_secret"] == "secret-with-dashes_and_underscores"
        assert channel["aix_preview_url"] == "http://local.teamclaw.net:8001/preview"

    def test_multiple_channel_types(self, moltis_config_file):
        """Test managing multiple channel types."""
        # Add DingTalk channel
        TomlUtils.set(
            moltis_config_file,
            "channels.dingtalk.semanticflowali",
            NEW_CHANNEL_CONFIG,
        )

        # Add WeChat channel
        TomlUtils.set(
            moltis_config_file,
            "channels.wechat.official",
            {"app_id": "wx123", "secret": "wx_secret"},
        )

        # Add Telegram channel
        TomlUtils.set(
            moltis_config_file,
            "channels.telegram.my_bot",
            {"token": "bot_token", "allowed_users": ["user1"]},
        )

        # Verify all exist
        assert TomlUtils.get(moltis_config_file, "channels.dingtalk.semanticflowali") is not None
        assert TomlUtils.get(moltis_config_file, "channels.wechat.official") is not None
        assert TomlUtils.get(moltis_config_file, "channels.telegram.my_bot") is not None


class TestTomlFileExplicitSave:
    """Test TomlFile class with explicit save pattern."""

    def test_tomlfile_load_and_get(self, moltis_config_file):
        """Test loading with TomlFile and getting values."""
        toml = TomlFile.load(moltis_config_file)

        # Get values without saving
        assert toml.get("server.port") == 20001
        assert toml.get("server.bind") == "0.0.0.0"

        # Get channels using generic key path
        channels = toml.get("channels")
        assert "telegram" in channels

    def test_tomlfile_set_and_save(self, tmp_path):
        """Test TomlFile set with explicit save."""
        # Copy fixture to temp location
        temp_file = tmp_path / "moltis_test.toml"
        shutil.copy(MOLTIS_CONFIG_FILE, temp_file)

        # Load, modify, then save
        toml = TomlFile.load(temp_file)
        toml.set("channels.dingtalk.semanticflowali.client_id", "new_id_123")
        toml.set("channels.dingtalk.semanticflowali.dm_policy", "disabled")

        # Before save, changes are in memory only
        assert toml.get("channels.dingtalk.semanticflowali.client_id") == "new_id_123"

        # Save to disk
        toml.save()

        # Verify saved to disk by reloading
        reloaded = TomlFile.load(temp_file)
        assert reloaded.get("channels.dingtalk.semanticflowali.client_id") == "new_id_123"
        assert reloaded.get("channels.dingtalk.semanticflowali.dm_policy") == "disabled"

    def test_tomlfile_add_nested_config_and_save(self, tmp_path):
        """Test adding nested config with TomlFile and explicit save."""
        temp_file = tmp_path / "moltis_test.toml"
        shutil.copy(MOLTIS_CONFIG_FILE, temp_file)

        # Load and add nested config using generic set
        toml = TomlFile.load(temp_file)
        toml.set("channels.dingtalk.newchannel", {
            "client_id": "ding_xxx",
            "client_secret": "secret_xxx",
            "dm_policy": "open",
        })

        # Config exists in memory
        channel = toml.get("channels.dingtalk.newchannel")
        assert channel is not None
        assert channel["client_id"] == "ding_xxx"

        # Save
        toml.save()

        # Verify by reading file content
        content = temp_file.read_text()
        assert "[channels.dingtalk.newchannel]" in content
        assert 'client_id = "ding_xxx"' in content

        # Verify by reloading
        reloaded = TomlFile.load(temp_file)
        assert reloaded.get("channels.dingtalk.newchannel") is not None

    def test_tomlfile_batch_operations_then_save(self, tmp_path):
        """Test batch operations with single save."""
        temp_file = tmp_path / "moltis_test.toml"
        shutil.copy(MOLTIS_CONFIG_FILE, temp_file)

        toml = TomlFile.load(temp_file)

        # Multiple operations using generic key paths
        toml.set("channels.dingtalk.batch_channel", NEW_CHANNEL_CONFIG)
        toml.set("server.port", 30001)
        toml.set("new_section.new_key", "new_value")

        # All in memory
        assert toml.get("channels.dingtalk.batch_channel") is not None
        assert toml.get("server.port") == 30001

        # Single save
        toml.save()

        # Verify all persisted
        reloaded = TomlFile.load(temp_file)
        assert reloaded.get("channels.dingtalk.batch_channel") is not None
        assert reloaded.get("server.port") == 30001
        assert reloaded.get("new_section.new_key") == "new_value"

    def test_tomlfile_modify_without_save_not_persisted(self, tmp_path):
        """Test that changes without save are not persisted."""
        temp_file = tmp_path / "moltis_test.toml"
        shutil.copy(MOLTIS_CONFIG_FILE, temp_file)

        # Get original content
        original_content = temp_file.read_text()

        # Load and modify without save
        toml = TomlFile.load(temp_file)
        toml.set("test_key", "test_value")
        toml.set("channels.dingtalk.unsaved_channel", {"client_id": "xxx"})

        # Don't save - discard changes by not calling save()
        del toml

        # Verify file unchanged
        current_content = temp_file.read_text()
        assert current_content == original_content

        # Verify reloading doesn't see changes
        reloaded = TomlFile.load(temp_file)
        assert reloaded.get("test_key") is None
        assert reloaded.get("channels.dingtalk.unsaved_channel") is None

    def test_tomlfile_array_operations_with_save(self, tmp_path):
        """Test array append/remove with TomlFile."""
        temp_file = tmp_path / "moltis_test.toml"
        shutil.copy(MOLTIS_CONFIG_FILE, temp_file)

        toml = TomlFile.load(temp_file)

        # Add config with allowlist
        toml.set("channels.dingtalk.test_channel", {
            "client_id": "test_id",
            "allowlist": ["*"],
        })

        # Modify array
        toml.remove("channels.dingtalk.test_channel.allowlist", "*")
        toml.append("channels.dingtalk.test_channel.allowlist", "100017")
        toml.append("channels.dingtalk.test_channel.allowlist", "61257")

        # Save
        toml.save()

        # Verify
        reloaded = TomlFile.load(temp_file)
        allowlist = reloaded.get("channels.dingtalk.test_channel.allowlist")
        assert allowlist == ["100017", "61257"]

    def test_tomlfile_delete_nested_config_with_save(self, tmp_path):
        """Test deleting nested config with TomlFile."""
        temp_file = tmp_path / "moltis_test.toml"
        shutil.copy(MOLTIS_CONFIG_FILE, temp_file)

        # Add configs
        toml = TomlFile.load(temp_file)
        toml.set("channels.dingtalk.channel1", {"client_id": "id1"})
        toml.set("channels.dingtalk.channel2", {"client_id": "id2"})
        toml.save()

        # Delete one
        toml = TomlFile.load(temp_file)
        result = toml.delete("channels.dingtalk.channel1")
        assert result is True
        toml.save()

        # Verify deletion
        reloaded = TomlFile.load(temp_file)
        assert reloaded.get("channels.dingtalk.channel1") is None
        assert reloaded.get("channels.dingtalk.channel2") is not None

    def test_tomlfile_dumps_and_to_dict(self, moltis_config_file):
        """Test TomlFile dumps and to_dict methods."""
        toml = TomlFile.load(moltis_config_file)

        # Test dumps
        toml_str = toml.dumps()
        assert "[server]" in toml_str
        assert "port = 20001" in toml_str

        # Test to_dict
        data = toml.to_dict()
        assert data["server"]["port"] == 20001
        assert "channels" in data

    def test_tomlfile_format_preserved_after_save(self, tmp_path):
        """Test that original comments and format are preserved after TomlFile save."""
        temp_file = tmp_path / "moltis_test.toml"
        shutil.copy(MOLTIS_CONFIG_FILE, temp_file)

        # Modify and save
        toml = TomlFile.load(temp_file)
        toml.set("server.port", 30001)
        toml.save()

        # Check preserved content
        saved_content = temp_file.read_text()
        assert "# Moltis Configuration" in saved_content
        assert "# ====================" in saved_content
        assert "# ══════════════════════════════════════════════════════════════════════════════" in saved_content

        # Verify new value saved
        assert "port = 30001" in saved_content

    def test_tomlfile_exists_check(self, tmp_path):
        """Test TomlFile exists method."""
        temp_file = tmp_path / "moltis_test.toml"
        shutil.copy(MOLTIS_CONFIG_FILE, temp_file)

        toml = TomlFile.load(temp_file)

        # Not exists initially
        assert toml.exists("channels.dingtalk.semanticflowali") is False

        # Add config (in memory)
        toml.set("channels.dingtalk.semanticflowali", NEW_CHANNEL_CONFIG)

        # Now exists in memory
        assert toml.exists("channels.dingtalk.semanticflowali") is True

        # Save and reload
        toml.save()
        reloaded = TomlFile.load(temp_file)

        # Still exists after save/reload
        assert reloaded.exists("channels.dingtalk.semanticflowali") is True

        # Other configs don't exist
        assert reloaded.exists("channels.dingtalk.otherchannel") is False
        assert reloaded.exists("channels.wechat.semanticflowali") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
