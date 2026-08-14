"""Unit tests for BareLoggerPlugin helpers — _resolve_log_level and _TraceIdFilter."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from gateway.community.plugins.logger.bare._plugin import (
    BareLoggerPlugin,
    _resolve_log_level,
    _TraceIdFilter,
)

# ── _resolve_log_level ──────────────────────────────────────────────────────


class TestResolveLogLevel:
    @pytest.mark.parametrize(
        ("level_str", "expected"),
        [
            ("DEBUG", logging.DEBUG),
            ("INFO", logging.INFO),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
            ("CRITICAL", logging.CRITICAL),
        ],
    )
    def test_standard_names(self, level_str: str, expected: int) -> None:
        assert _resolve_log_level(level_str) == expected

    @pytest.mark.parametrize(
        ("level_str", "expected"),
        [
            ("debug", logging.DEBUG),
            ("info", logging.INFO),
            ("warning", logging.WARNING),
            ("error", logging.ERROR),
            ("critical", logging.CRITICAL),
        ],
    )
    def test_case_insensitive(self, level_str: str, expected: int) -> None:
        assert _resolve_log_level(level_str) == expected

    @pytest.mark.parametrize(
        ("level_str", "expected"),
        [
            ("  DEBUG  ", logging.DEBUG),
            (" info ", logging.INFO),
            ("\tWARNING\n", logging.WARNING),
        ],
    )
    def test_whitespace_stripped(self, level_str: str, expected: int) -> None:
        assert _resolve_log_level(level_str) == expected

    def test_numeric_string(self) -> None:
        assert _resolve_log_level("10") == 10
        assert _resolve_log_level("20") == 20
        assert _resolve_log_level("40") == 40

    def test_numeric_string_with_whitespace(self) -> None:
        assert _resolve_log_level("  30 ") == 30

    def test_invalid_string_returns_info(self) -> None:
        assert _resolve_log_level("not-a-level") == logging.INFO

    def test_empty_string_returns_info(self) -> None:
        assert _resolve_log_level("") == logging.INFO


# ── _TraceIdFilter ───────────────────────────────────────────────────────────


class TestTraceIdFilter:
    def test_filter_returns_true(self) -> None:
        f = _TraceIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        assert f.filter(record) is True

    def test_filter_injects_traceid(self) -> None:
        f = _TraceIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        mock_tracer = MagicMock()
        mock_tracer.get_trace_id.return_value = "abc123def456"
        with patch(
            "gateway.community.tracer.get_tracer_plugin",
            return_value=mock_tracer,
        ):
            f.filter(record)
        assert record.traceid == "abc123def456"

    def test_filter_injects_dash_when_no_span(self) -> None:
        f = _TraceIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        mock_tracer = MagicMock()
        mock_tracer.get_trace_id.return_value = "-"
        with patch(
            "gateway.community.tracer.get_tracer_plugin",
            return_value=mock_tracer,
        ):
            f.filter(record)
        assert record.traceid == "-"


# ── BareLoggerPlugin ──────────────────────────────────────────────────────────


class TestBareLoggerPlugin:
    def test_get_logger_default_name(self) -> None:
        plugin = BareLoggerPlugin()
        plugin.configure(app_name="gw-test")
        log = plugin.get_logger()
        assert isinstance(log, logging.Logger)
        assert log.name == "gw-test"

    def test_get_logger_named(self) -> None:
        plugin = BareLoggerPlugin()
        plugin.configure(app_name="gw-test")
        log = plugin.get_logger("custom-logger")
        assert log.name == "custom-logger"

    def test_get_logger_caches_instance(self) -> None:
        plugin = BareLoggerPlugin()
        plugin.configure(app_name="gw-test")
        log1 = plugin.get_logger("cached")
        log2 = plugin.get_logger("cached")
        assert log1 is log2

    def test_get_logger_different_names(self) -> None:
        plugin = BareLoggerPlugin()
        plugin.configure(app_name="gw-test")
        log1 = plugin.get_logger("first")
        log2 = plugin.get_logger("second")
        assert log1 is not log2
        assert log1.name == "first"
        assert log2.name == "second"

    def test_configure_sets_log_level(self, tmp_path: object) -> None:
        plugin = BareLoggerPlugin()
        plugin.configure(
            log_level="DEBUG",
            log_dir="/tmp/gw-test-logs",
            app_name="gw-test",
        )
        assert plugin._level == logging.DEBUG

    def test_configure_creates_log_dir(self, tmp_path: object) -> None:
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = os.path.join(tmpdir, "nested", "logs")
            plugin = BareLoggerPlugin()
            plugin.configure(
                log_level="INFO",
                log_dir=log_dir,
                app_name="gw-test",
            )
            assert os.path.isdir(log_dir)

    def test_configure_with_default_log_dir(self) -> None:
        plugin = BareLoggerPlugin()
        plugin.configure(app_name="gw-default-dir-test")
        # When log_dir is not provided, it defaults to ~/logs/{app_name}
        assert plugin._log_dir.endswith("logs/gw-default-dir-test")

    def test_logger_has_file_handlers_after_configure(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            plugin = BareLoggerPlugin()
            plugin.configure(
                log_level="DEBUG",
                log_dir=tmpdir,
                app_name="gw-handler-test",
            )
            log = plugin.get_logger("handler-check")
            # Should have 4 file handlers (log, error, fatal, common-error)
            file_handlers = [
                h
                for h in log.handlers
                if isinstance(h, logging.handlers.TimedRotatingFileHandler)
            ]
            assert len(file_handlers) == 4
            # Clean up
            for h in file_handlers:
                h.close()

    def test_logger_without_log_dir_has_no_handlers(self) -> None:
        """Logger configured before configure() has no file handlers."""
        plugin = BareLoggerPlugin()
        # Don't call configure() — _log_dir stays as default ""
        log = plugin.get_logger("no-dir")
        file_handlers = [
            h
            for h in log.handlers
            if isinstance(h, logging.handlers.TimedRotatingFileHandler)
        ]
        # _log_dir defaults to "" which causes _configure_logger to return early
        # But get_logger calls _configure_logger before configure is called,
        # so handlers should not be added.
        assert len(file_handlers) == 0
