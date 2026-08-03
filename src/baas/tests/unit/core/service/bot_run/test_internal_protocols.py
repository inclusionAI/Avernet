"""Structural protocol conformance tests for internal bot_run protocols.

Verifies that BotService Protocol correctly defines expected methods with
proper signatures, and that implementations conform.
"""

from typing import Protocol, get_type_hints

from secbaas.community.api.bot_runtime import BotResponse
from secbaas.community.core.service.bot_run import BotService


class TestBotServiceProtocol:
    """Tests for the BotService Protocol definition."""

    def test_is_protocol(self):
        """THEN BotService is a Protocol class."""
        assert issubclass(BotService, Protocol)

    def test_all_methods_defined(self):
        """THEN Protocol defines all expected methods."""
        expected = {
            "create_session",
            "send_message",
            "inject_message",
            "abort_run",
            "get_messages",
        }
        protocol_methods = {
            name for name in dir(BotService) if not name.startswith("_")
        }
        for method in expected:
            assert method in protocol_methods, f"Missing method: {method}"

    def test_create_session_signature(self):
        """THEN create_session returns SessionInfo."""
        # Use __annotations__ directly to avoid TYPE_CHECKING resolution
        raw = BotService.create_session.__annotations__
        assert "return" in raw
        assert "SessionInfo" in str(raw["return"])

    def test_send_message_signature(self):
        """THEN send_message returns BotResponse."""
        hints = get_type_hints(BotService.send_message)
        assert hints["return"] == BotResponse
