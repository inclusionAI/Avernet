"""Unit tests for api/bot_manager/_enums.py — Bot management enums and constants."""

from enum import StrEnum

from secbaas.community.api.bot_manage import BotStatus, SlaGrade


class TestSlaGrade:
    """Tests for SlaGrade constants class."""

    def test_standard(self):
        assert SlaGrade.STANDARD == "standard"

    def test_enterprise(self):
        assert SlaGrade.ENTERPRISE == "enterprise"

    def test_is_not_enum(self):
        """THEN SlaGrade is a plain class, not an Enum."""
        assert not issubclass(type(SlaGrade()), StrEnum)


class TestBotStatus:
    """Tests for BotStatus StrEnum."""

    def test_values(self):
        assert BotStatus.PENDING == "PENDING"
        assert BotStatus.ACTIVE == "ACTIVE"
        assert BotStatus.DESTROYING == "DESTROYING"
        assert BotStatus.FAILED == "FAILED"
        assert BotStatus.RELEASED == "RELEASED"

    def test_is_str_enum(self):
        """THEN BotStatus is a StrEnum."""
        assert issubclass(BotStatus, StrEnum)

    def test_str_comparison(self):
        """THEN BotStatus values compare with strings."""
        assert BotStatus.ACTIVE == "ACTIVE"

    def test_unique_values(self):
        """THEN all status values are unique."""
        values = [s.value for s in BotStatus]
        assert len(values) == len(set(values))
