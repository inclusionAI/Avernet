"""Tests for expert chat SQLite models.

Focuses on edge cases like JSON deserialization error handling.
"""
import pytest

from agentclaw.community.core.expert_chat.sqlite_models import (
    AcExpertChatBotSession,
    AcExpertChatInstance,
)


class TestAcExpertChatBotSessionToDict:
    """Tests for AcExpertChatBotSession.to_dict method."""

    def test_to_dict_with_none_timestamps(self):
        """to_dict handles None timestamps gracefully."""
        session = AcExpertChatBotSession(
            user_id="u1",
            bot_id="b1",
            owner_id="o1",
            status="ACTIVE",
            session_key="sk1",
            env="test",
            gmt_create=None,
            gmt_modified=None,
        )
        result = session.to_dict()
        assert result["gmt_create"] is None
        assert result["gmt_modified"] is None

    def test_to_dict_with_valid_timestamps(self):
        """to_dict formats timestamps as ISO strings."""
        from datetime import datetime
        now = datetime(2024, 1, 15, 10, 30, 0)
        session = AcExpertChatBotSession(
            user_id="u1",
            bot_id="b1",
            owner_id="o1",
            status="ACTIVE",
            session_key="sk1",
            env="test",
            gmt_create=now,
            gmt_modified=now,
        )
        result = session.to_dict()
        assert result["gmt_create"] == "2024-01-15T10:30:00"
        assert result["gmt_modified"] == "2024-01-15T10:30:00"


class TestAcExpertChatInstanceToDict:
    """Tests for AcExpertChatInstance.to_dict method (lines 132-133)."""

    def test_to_dict_with_valid_json_ext(self):
        """to_dict parses valid JSON ext correctly."""
        instance = AcExpertChatInstance(
            bot_id="b1",
            owner_id="o1",
            user_id="u1",
            status="success",
            ext='{"bot_uuid": "uuid-123", "version": 1}',
            env="test",
        )
        result = instance.to_dict()
        assert result["ext"] == {"bot_uuid": "uuid-123", "version": 1}

    def test_to_dict_with_null_ext(self):
        """to_dict handles None ext."""
        instance = AcExpertChatInstance(
            bot_id="b1",
            owner_id="o1",
            user_id="u1",
            status="init",
            ext=None,
            env="test",
        )
        result = instance.to_dict()
        assert result["ext"] is None

    def test_to_dict_with_empty_string_ext(self):
        """to_dict handles empty string ext."""
        instance = AcExpertChatInstance(
            bot_id="b1",
            owner_id="o1",
            user_id="u1",
            status="init",
            ext="",
            env="test",
        )
        result = instance.to_dict()
        assert result["ext"] is None

    def test_to_dict_with_invalid_json_ext_returns_none(self):
        """Lines 132-133: to_dict returns None for invalid JSON ext.

        This covers the exception handling in lines 130-133 where
        json.loads raises TypeError or ValueError.
        """
        instance = AcExpertChatInstance(
            bot_id="b1",
            owner_id="o1",
            user_id="u1",
            status="init",
            ext="not valid json {{{",
            env="test",
        )
        result = instance.to_dict()
        # Invalid JSON should result in None (caught by except block)
        assert result["ext"] is None

    def test_to_dict_with_json_array_ext(self):
        """to_dict handles JSON array ext."""
        instance = AcExpertChatInstance(
            bot_id="b1",
            owner_id="o1",
            user_id="u1",
            status="init",
            ext='[1, 2, 3]',
            env="test",
        )
        result = instance.to_dict()
        assert result["ext"] == [1, 2, 3]

    def test_to_dict_with_json_string_ext(self):
        """to_dict handles JSON string ext."""
        instance = AcExpertChatInstance(
            bot_id="b1",
            owner_id="o1",
            user_id="u1",
            status="init",
            ext='"just a string"',
            env="test",
        )
        result = instance.to_dict()
        assert result["ext"] == "just a string"