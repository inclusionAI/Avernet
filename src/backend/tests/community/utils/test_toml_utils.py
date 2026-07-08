"""
Tests for TomlUtils class.
"""
import tempfile
from pathlib import Path

import pytest

from agentclaw.community.utils.toml_utils import TomlUtils


# Sample TOML content with nested tables like [channels.dingtalk.semanticflowali]
SAMPLE_TOML = """
[channels.dingtalk.semanticflowali]
client_id = "dingl6x1pu7l5y9itkut"
client_secret = "cXi3cLfK17vT4i4z-j08qdEy_Pb8Gj-9ZJwl61jpsJHl7oVNLb9MAv4wiMPgoHYF"
dm_policy = "open"
allowlist = ["*"]
reply_to_message = true
robot_code = "dingl6x1pu7l5y9itkut"
card_template_id = "4d98ff02-25be-437b-933d-9884bac9acca.schema"
card_template_key = "content"
enable_streaming_cards = true
aix_enable = true
aix_preview_url = "http://local.teamclaw.net:8001/preview"
include_sender_name = true

[channels.dingtalk.anotherchannel]
client_id = "another_id"
dm_policy = "disabled"
allowlist = ["user1", "user2"]

[channels.wechat.official]
app_id = "wx123456"
secret = "secret_key"
"""


@pytest.fixture
def temp_toml_file():
    """Create a temporary TOML file with sample content."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(SAMPLE_TOML)
        f.flush()
        yield Path(f.name)
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def new_toml_file():
    """Create a new empty TOML file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write("")
        f.flush()
        yield Path(f.name)
    Path(f.name).unlink(missing_ok=True)


class TestTomlUtilsLoad:
    """Test loading TOML files."""

    def test_load_as_dict(self, temp_toml_file):
        """Test loading TOML as dict."""
        data = TomlUtils.load(temp_toml_file)

        assert "channels" in data
        assert "dingtalk" in data["channels"]
        assert "semanticflowali" in data["channels"]["dingtalk"]
        assert data["channels"]["dingtalk"]["semanticflowali"]["client_id"] == "dingl6x1pu7l5y9itkut"

    def test_load_as_raw(self, temp_toml_file):
        """Test loading TOML as raw TOMLDocument."""
        doc = TomlUtils.load(temp_toml_file, raw=True)

        assert doc["channels"]["dingtalk"]["semanticflowali"]["client_id"] == "dingl6x1pu7l5y9itkut"

    def test_loads(self):
        """Test loading from string."""
        data = TomlUtils.loads(SAMPLE_TOML)

        assert "channels" in data
        assert data["channels"]["dingtalk"]["semanticflowali"]["dm_policy"] == "open"


class TestTomlUtilsGet:
    """Test getting values."""

    def test_get_simple(self, temp_toml_file):
        """Test getting simple value."""
        value = TomlUtils.get(
            temp_toml_file, "channels.dingtalk.semanticflowali.client_id"
        )
        assert value == "dingl6x1pu7l5y9itkut"

    def test_get_array(self, temp_toml_file):
        """Test getting array value."""
        value = TomlUtils.get(
            temp_toml_file, "channels.dingtalk.semanticflowali.allowlist"
        )
        assert value == ["*"]

    def test_get_boolean(self, temp_toml_file):
        """Test getting boolean value."""
        value = TomlUtils.get(
            temp_toml_file, "channels.dingtalk.semanticflowali.reply_to_message"
        )
        assert value is True

    def test_get_nonexistent(self, temp_toml_file):
        """Test getting non-existent key returns default."""
        value = TomlUtils.get(
            temp_toml_file, "channels.dingtalk.semanticflowali.nonexistent", default="default"
        )
        assert value == "default"

    def test_get_nested_table(self, temp_toml_file):
        """Test getting entire nested table."""
        value = TomlUtils.get(temp_toml_file, "channels.dingtalk.semanticflowali")
        assert isinstance(value, dict)
        assert value["client_id"] == "dingl6x1pu7l5y9itkut"


class TestTomlUtilsSet:
    """Test setting values."""

    def test_set_existing_value(self, temp_toml_file):
        """Test updating existing value."""
        TomlUtils.set(
            temp_toml_file,
            "channels.dingtalk.semanticflowali.client_id",
            "new_client_id"
        )

        value = TomlUtils.get(
            temp_toml_file, "channels.dingtalk.semanticflowali.client_id"
        )
        assert value == "new_client_id"

    def test_set_new_value(self, temp_toml_file):
        """Test adding new value."""
        TomlUtils.set(
            temp_toml_file,
            "channels.dingtalk.semanticflowali.new_field",
            "new_value"
        )

        value = TomlUtils.get(
            temp_toml_file, "channels.dingtalk.semanticflowali.new_field"
        )
        assert value == "new_value"

    def test_set_creates_nested_tables(self, new_toml_file):
        """Test setting creates nested tables automatically."""
        TomlUtils.set(
            new_toml_file,
            "channels.dingtalk.newchannel.client_id",
            "xxx"
        )

        value = TomlUtils.get(new_toml_file, "channels.dingtalk.newchannel.client_id")
        assert value == "xxx"


class TestTomlUtilsDelete:
    """Test deleting values."""

    def test_delete_existing(self, temp_toml_file):
        """Test deleting existing key."""
        result = TomlUtils.delete(
            temp_toml_file, "channels.dingtalk.semanticflowali.client_id"
        )
        assert result is True

        value = TomlUtils.get(
            temp_toml_file, "channels.dingtalk.semanticflowali.client_id"
        )
        assert value is None

    def test_delete_nonexistent(self, temp_toml_file):
        """Test deleting non-existent key returns False."""
        result = TomlUtils.delete(
            temp_toml_file, "channels.dingtalk.semanticflowali.nonexistent"
        )
        assert result is False


class TestTomlUtilsExists:
    """Test checking existence."""

    def test_exists_true(self, temp_toml_file):
        """Test checking existing key."""
        result = TomlUtils.exists(
            temp_toml_file, "channels.dingtalk.semanticflowali.client_id"
        )
        assert result is True

    def test_exists_false(self, temp_toml_file):
        """Test checking non-existing key."""
        result = TomlUtils.exists(
            temp_toml_file, "channels.dingtalk.semanticflowali.nonexistent"
        )
        assert result is False


class TestTomlUtilsArray:
    """Test array operations."""

    def test_append_to_array(self, temp_toml_file):
        """Test appending to array."""
        TomlUtils.append(
            temp_toml_file,
            "channels.dingtalk.semanticflowali.allowlist",
            "new_user"
        )

        value = TomlUtils.get(
            temp_toml_file, "channels.dingtalk.semanticflowali.allowlist"
        )
        assert "*" in value
        assert "new_user" in value

    def test_remove_from_array(self, temp_toml_file):
        """Test removing from array."""
        result = TomlUtils.remove(
            temp_toml_file,
            "channels.dingtalk.anotherchannel.allowlist",
            "user1"
        )
        assert result is True

        value = TomlUtils.get(
            temp_toml_file, "channels.dingtalk.anotherchannel.allowlist"
        )
        assert "user1" not in value
        assert "user2" in value


class TestTomlUtilsBatchSet:
    """Test batch operations."""

    def test_batch_set(self, temp_toml_file):
        """Test setting multiple values."""
        TomlUtils.batch_set(temp_toml_file, {
            "channels.dingtalk.semanticflowali.client_id": "new_id",
            "channels.dingtalk.semanticflowali.dm_policy": "disabled",
            "channels.dingtalk.semanticflowali.new_field": "new_value"
        })

        assert TomlUtils.get(
            temp_toml_file, "channels.dingtalk.semanticflowali.client_id"
        ) == "new_id"
        assert TomlUtils.get(
            temp_toml_file, "channels.dingtalk.semanticflowali.dm_policy"
        ) == "disabled"
        assert TomlUtils.get(
            temp_toml_file, "channels.dingtalk.semanticflowali.new_field"
        ) == "new_value"


class TestNestedKeyOperations:
    """Test nested key operations (e.g., channels.dingtalk.xxx)."""

    def test_get_nested_table(self, temp_toml_file):
        """Test getting nested table like channels.dingtalk.semanticflowali."""
        channel = TomlUtils.get(temp_toml_file, "channels.dingtalk.semanticflowali")

        assert channel is not None
        assert channel["client_id"] == "dingl6x1pu7l5y9itkut"
        assert channel["dm_policy"] == "open"

    def test_get_nonexistent_nested_table(self, temp_toml_file):
        """Test getting non-existent nested table."""
        channel = TomlUtils.get(temp_toml_file, "channels.dingtalk.nonexistent")
        assert channel is None

    def test_set_nested_table(self, new_toml_file):
        """Test setting nested table."""
        TomlUtils.set(
            new_toml_file,
            "channels.dingtalk.newchannel",
            {
                "client_id": "new_id",
                "client_secret": "new_secret",
                "dm_policy": "open",
                "allowlist": ["*"],
            }
        )

        channel = TomlUtils.get(new_toml_file, "channels.dingtalk.newchannel")
        assert channel is not None
        assert channel["client_id"] == "new_id"
        assert channel["dm_policy"] == "open"

    def test_delete_nested_table(self, temp_toml_file):
        """Test deleting nested table."""
        result = TomlUtils.delete(temp_toml_file, "channels.dingtalk.anotherchannel")
        assert result is True

        channel = TomlUtils.get(temp_toml_file, "channels.dingtalk.anotherchannel")
        assert channel is None

    def test_batch_update_nested_table(self, temp_toml_file):
        """Test batch updating nested table fields."""
        TomlUtils.batch_set(temp_toml_file, {
            "channels.dingtalk.semanticflowali.client_id": "updated_id",
            "channels.dingtalk.semanticflowali.dm_policy": "disabled",
        })

        channel = TomlUtils.get(temp_toml_file, "channels.dingtalk.semanticflowali")
        assert channel["client_id"] == "updated_id"
        assert channel["dm_policy"] == "disabled"
        # Other fields should remain
        assert channel["client_secret"] == "cXi3cLfK17vT4i4z-j08qdEy_Pb8Gj-9ZJwl61jpsJHl7oVNLb9MAv4wiMPgoHYF"

    def test_exists_nested_table(self, temp_toml_file):
        """Test checking if nested table exists."""
        assert TomlUtils.exists(temp_toml_file, "channels.dingtalk.semanticflowali") is True
        assert TomlUtils.exists(temp_toml_file, "channels.dingtalk.nonexistent") is False


class TestTomlUtilsDump:
    """Test dumping to file."""

    def test_dump_dict(self, new_toml_file):
        """Test dumping dict to file."""
        data = {
            "channels": {
                "dingtalk": {
                    "test": {
                        "client_id": "test_id"
                    }
                }
            }
        }
        TomlUtils.dump(new_toml_file, data)

        loaded = TomlUtils.load(new_toml_file)
        assert loaded["channels"]["dingtalk"]["test"]["client_id"] == "test_id"

    def test_dumps(self):
        """Test dumping to string."""
        data = {"key": "value", "nested": {"a": 1}}
        toml_str = TomlUtils.dumps(data)

        assert "key = \"value\"" in toml_str
        assert "[nested]" in toml_str
        assert "a = 1" in toml_str


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
