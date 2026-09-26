"""Structural protocol conformance tests for bot_runtime protocols.

Verifies that all Protocols correctly define expected methods with
proper signatures, and that implementations conform.

Note: BotService Protocol tests have been moved to
tests/unit/core/service/bot_run/test_internal_protocols.py
since BotService is now defined in core/service/bot_run.
"""

from typing import Any, Protocol, get_type_hints

from secbaas.community.api.bot_runtime import (
    BotCmdDispatcher,
    BotHttpDispatcher,
    BotWssDispatcher,
    WsConnectionInfo,
)
from secbaas.community.api.device_manage import CommandResult


class TestBotCmdDispatcherProtocol:
    """Tests for the BotCmdDispatcher Protocol definition."""

    def test_is_protocol(self):
        """THEN BotCmdDispatcher is a Protocol class."""
        assert issubclass(BotCmdDispatcher, Protocol)

    def test_required_method_defined(self):
        """THEN dispatch_bot_execute_command is defined."""
        assert hasattr(BotCmdDispatcher, "dispatch_bot_execute_command")

    def test_method_is_async(self):
        """THEN dispatch_bot_execute_command is a coroutine."""
        import inspect

        method = BotCmdDispatcher.dispatch_bot_execute_command
        assert inspect.iscoroutinefunction(method)

    def test_return_type_is_command_result(self):
        """THEN dispatch_bot_execute_command returns CommandResult."""
        hints = get_type_hints(BotCmdDispatcher.dispatch_bot_execute_command)
        assert hints["return"] == CommandResult


class TestBotHttpDispatcherProtocol:
    """Tests for the BotHttpDispatcher Protocol definition."""

    def test_is_protocol(self):
        """THEN BotHttpDispatcher is a Protocol class."""
        assert issubclass(BotHttpDispatcher, Protocol)

    def test_required_method_defined(self):
        """THEN dispatch_bot_http_invoke is defined."""
        assert hasattr(BotHttpDispatcher, "dispatch_bot_http_invoke")

    def test_method_is_async(self):
        """THEN dispatch_bot_http_invoke is a coroutine."""
        import inspect

        method = BotHttpDispatcher.dispatch_bot_http_invoke
        assert inspect.iscoroutinefunction(method)

    def test_return_type_is_dict(self):
        """THEN dispatch_bot_http_invoke returns dict."""
        hints = get_type_hints(BotHttpDispatcher.dispatch_bot_http_invoke)
        assert hints["return"] == dict[str, Any]


class TestBotWssDispatcherProtocol:
    """Tests for the BotWssDispatcher Protocol definition."""

    def test_is_protocol(self):
        """THEN BotWssDispatcher is a Protocol class."""
        assert issubclass(BotWssDispatcher, Protocol)

    def test_required_method_defined(self):
        """THEN dispatch_bot_ws_conn_info is defined."""
        assert hasattr(BotWssDispatcher, "dispatch_bot_ws_conn_info")

    def test_method_is_async(self):
        """THEN dispatch_bot_ws_conn_info is a coroutine."""
        import inspect

        method = BotWssDispatcher.dispatch_bot_ws_conn_info
        assert inspect.iscoroutinefunction(method)

    def test_return_type_is_ws_connection_info(self):
        """THEN dispatch_bot_ws_conn_info returns WsConnectionInfo."""
        hints = get_type_hints(BotWssDispatcher.dispatch_bot_ws_conn_info)
        assert hints["return"] == WsConnectionInfo
