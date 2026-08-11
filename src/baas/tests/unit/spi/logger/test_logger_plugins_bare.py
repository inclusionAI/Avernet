"""Tests for BareLoggerPlugin protocol compliance and implementations.

SofaLoggerPlugin tests moved to secbaas.enterprise.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.plugins.logger.bare import BareLoggerPlugin
from secbaas.community.plugins.logger.bare._plugin import _resolve_log_level
from secbaas.community.spi.logger._protocols import LoggerPlugin


def _has_logger_plugin_attrs(obj: object) -> bool:
    """Check structural conformance to LoggerPlugin without isinstance."""
    return hasattr(obj, "get_logger") and hasattr(obj, "configure")


class TestLoggerProtocolCompliance:
    """Structural conformance tests — any object with the right methods
    satisfies the LoggerPlugin protocol."""

    def test_baas_logger_plugin_has_required_methods(self) -> None:
        assert _has_logger_plugin_attrs(BareLoggerPlugin())

    def test_get_logger_returns_logger_instance(self) -> None:
        plugin = BareLoggerPlugin()
        log = plugin.get_logger("test")
        assert hasattr(log, "info")
        assert hasattr(log, "error")
        assert hasattr(log, "debug")

    def test_configure_accepted(self) -> None:
        BareLoggerPlugin().configure()


class TestBaasLoggerPlugin:
    """Tests for baas-mode logger plugin."""

    def test_get_logger_creates_new_logger(self) -> None:
        plugin = BareLoggerPlugin()
        logger = plugin.get_logger("test_module")

        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_module"

    def test_get_logger_returns_same_instance(self) -> None:
        plugin = BareLoggerPlugin()
        l1 = plugin.get_logger("dup")
        l2 = plugin.get_logger("dup")

        assert l1 is l2

    def test_get_logger_default_name_uses_app_name(self) -> None:
        plugin = BareLoggerPlugin()
        plugin.configure(app_name="myapp")
        logger = plugin.get_logger()

        assert logger.name == "myapp"

    def test_configure_sets_log_level(self) -> None:
        plugin = BareLoggerPlugin()
        plugin.configure(log_level="DEBUG", app_name="test")

        assert plugin._level == logging.DEBUG

    def test_configure_creates_file_handlers(self) -> None:
        plugin = BareLoggerPlugin()

        with (
            patch("os.makedirs"),
            patch(
                "secbaas.community.plugins.logger.bare._plugin.TimedRotatingFileHandler"
            ) as mock_handler_cls,
        ):
            mock_handler_cls.side_effect = lambda *a, **kw: MagicMock()

            plugin.configure(app_name="test", log_dir="/tmp/test_logs")
            logger = plugin.get_logger("test")

            assert len(logger.handlers) == 4

    def test_configure_creates_root_stream_handler(self) -> None:
        plugin = BareLoggerPlugin()

        with (
            patch("os.makedirs"),
            patch(
                "secbaas.community.plugins.logger.bare._plugin.TimedRotatingFileHandler"
            ),
        ):
            plugin.configure(app_name="test", log_dir="/tmp/test_logs")

            root = logging.getLogger()
            stream_handlers = [h for h in root.handlers if hasattr(h, "stream")]
            assert len(stream_handlers) >= 1

    def test_trace_id_filter_injects_traceid(self) -> None:
        mock_tracer = MagicMock()
        mock_tracer.get_trace_id.return_value = "abc123"
        with patch(
            "secbaas.community.tracer.get_tracer_plugin", return_value=mock_tracer
        ):
            from secbaas.community.plugins.logger.bare._plugin import _TraceIdFilter

            f = _TraceIdFilter()
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="test",
                args=(),
                exc_info=None,
            )

            result = f.filter(record)
            assert result is True
            assert record.traceid == "abc123"

    def test_resolve_log_level(self) -> None:
        assert _resolve_log_level("DEBUG") == logging.DEBUG
        assert _resolve_log_level("INFO") == logging.INFO
        assert _resolve_log_level("WARNING") == logging.WARNING
        assert _resolve_log_level("ERROR") == logging.ERROR
        assert _resolve_log_level("CRITICAL") == logging.CRITICAL
        assert _resolve_log_level("10") == logging.DEBUG
        assert _resolve_log_level("invalid") == logging.INFO
