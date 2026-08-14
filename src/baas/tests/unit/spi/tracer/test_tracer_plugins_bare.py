"""Tests for TracerPlugin protocol compliance and implementations."""

from __future__ import annotations

import importlib.util
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.spi.tracer._protocols import TracerPlugin

_HAS_OPENTELEMETRY = importlib.util.find_spec("opentelemetry") is not None

if _HAS_OPENTELEMETRY:
    from secbaas.community.plugins.tracer.bare import BareTracerPlugin


def _has_tracer_plugin_attrs(obj: object) -> bool:
    """Check structural conformance to TracerPlugin without isinstance."""
    return (
        hasattr(obj, "setup")
        and hasattr(obj, "install_middleware")
        and hasattr(obj, "get_trace_id")
        and hasattr(obj, "capture_context")
        and hasattr(obj, "attach_context")
        and hasattr(obj, "detach_context")
    )


class TestTracerProtocolCompliance:
    """Structural conformance tests."""

    def test_otlp_tracer_plugin_has_required_methods(self) -> None:
        if not _HAS_OPENTELEMETRY:
            pytest.skip("opentelemetry not installed")
        assert _has_tracer_plugin_attrs(BareTracerPlugin())

    def test_setup_no_error(self) -> None:
        if not _HAS_OPENTELEMETRY:
            pytest.skip("opentelemetry not installed")
        with (
            patch("opentelemetry.sdk.trace.TracerProvider"),
            patch("opentelemetry.sdk.trace.export.BatchSpanProcessor"),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch.dict("os.environ", {"OTEL_TRACES_EXPORTER": "none"}),
        ):
            BareTracerPlugin().setup("test-app")


@pytest.mark.skipif(not _HAS_OPENTELEMETRY, reason="opentelemetry not installed")
class TestOtlpTracerPlugin:
    """Tests for OpenTelemetry-based tracer plugin."""

    def test_setup_creates_tracer_provider(self) -> None:
        import opentelemetry.sdk.trace

        with (
            patch.object(
                opentelemetry.sdk.trace, "TracerProvider"
            ) as mock_provider_cls,
            patch("opentelemetry.trace.set_tracer_provider") as mock_set,
            patch.dict("os.environ", {"OTEL_TRACES_EXPORTER": "none"}),
        ):
            BareTracerPlugin().setup("test-app")

            mock_provider_cls.assert_called_once()
            mock_set.assert_called_once_with(mock_provider_cls.return_value)

    def test_setup_with_console_exporter(self) -> None:
        import opentelemetry.sdk.trace

        with (
            patch.object(opentelemetry.sdk.trace, "TracerProvider"),
            patch("opentelemetry.sdk.trace.export.BatchSpanProcessor"),
            patch("opentelemetry.sdk.trace.export.ConsoleSpanExporter"),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch.dict("os.environ", {"OTEL_TRACES_EXPORTER": "console"}),
        ):
            BareTracerPlugin().setup("test-app")

    def test_install_middleware_adds_otel_middleware(self) -> None:
        import opentelemetry.instrumentation.asgi

        with patch.object(
            opentelemetry.instrumentation.asgi, "OpenTelemetryMiddleware"
        ) as mock_mw:
            app = MagicMock()
            BareTracerPlugin().install_middleware(app)

            app.add_middleware.assert_called_once_with(mock_mw)

    def test_get_trace_id_with_valid_span(self) -> None:
        import opentelemetry.trace

        mock_ctx = MagicMock()
        mock_ctx.is_valid = True
        mock_ctx.trace_id = 0xABCD1234

        mock_span = MagicMock()
        mock_span.get_span_context.return_value = mock_ctx

        with patch.object(
            opentelemetry.trace, "get_current_span", return_value=mock_span
        ):
            result = BareTracerPlugin().get_trace_id()
            assert result == "000000000000000000000000abcd1234"

    def test_get_trace_id_with_no_span(self) -> None:
        import opentelemetry.trace

        with patch.object(opentelemetry.trace, "get_current_span", return_value=None):
            assert BareTracerPlugin().get_trace_id() == "-"

    def test_get_trace_id_with_invalid_span(self) -> None:
        import opentelemetry.trace

        mock_ctx = MagicMock()
        mock_ctx.is_valid = False

        mock_span = MagicMock()
        mock_span.get_span_context.return_value = mock_ctx

        with patch.object(
            opentelemetry.trace, "get_current_span", return_value=mock_span
        ):
            assert BareTracerPlugin().get_trace_id() == "-"

    def test_capture_context_returns_none_when_no_valid_span(self) -> None:
        import opentelemetry.context
        import opentelemetry.trace

        mock_span = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.is_valid = False
        mock_span.get_span_context.return_value = mock_ctx

        with (
            patch.object(opentelemetry.context, "get_current", return_value={}),
            patch.object(
                opentelemetry.trace, "get_current_span", return_value=mock_span
            ),
        ):
            assert BareTracerPlugin().capture_context() is None

    def test_capture_context_returns_context_when_valid_span(self) -> None:
        import opentelemetry.context
        import opentelemetry.trace

        mock_ctx = MagicMock()
        mock_ctx.is_valid = True
        mock_span = MagicMock()
        mock_span.get_span_context.return_value = mock_ctx

        sentinel_ctx = object()
        with (
            patch.object(
                opentelemetry.context, "get_current", return_value=sentinel_ctx
            ),
            patch.object(
                opentelemetry.trace, "get_current_span", return_value=mock_span
            ),
        ):
            assert BareTracerPlugin().capture_context() is sentinel_ctx

    def test_attach_and_detach_context(self) -> None:
        import opentelemetry.context

        sentinel_token = object()
        with patch.object(
            opentelemetry.context, "attach", return_value=sentinel_token
        ) as mock_attach:
            plugin = BareTracerPlugin()
            token = plugin.attach_context({"fake": "ctx"})
            assert token is sentinel_token
            mock_attach.assert_called_once_with({"fake": "ctx"})

        with patch.object(opentelemetry.context, "detach") as mock_detach:
            plugin.detach_context(sentinel_token)
            mock_detach.assert_called_once_with(sentinel_token)
