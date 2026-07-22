from gateway.community.plugins.tracer.bare._plugin import BareTracerPlugin
from gateway.community.spi.tracer import TracerPlugin


class TracerPluginContract:
    plugin: TracerPlugin

    def test_get_trace_id_returns_string(self) -> None:
        trace_id = self.plugin.get_trace_id()
        assert isinstance(trace_id, str)

    def test_setup_does_not_raise(self) -> None:
        self.plugin.setup("test-app")

    def test_install_middleware_does_not_raise(self) -> None:
        from unittest.mock import MagicMock

        mock_app = MagicMock()
        self.plugin.install_middleware(mock_app)


class TestBareTracerPlugin(TracerPluginContract):
    def setup_method(self) -> None:
        self.plugin = BareTracerPlugin()

    def test_get_trace_id_returns_dash_when_no_span(self) -> None:
        assert self.plugin.get_trace_id() == "-"

    def test_get_trace_id_is_hex_string(self) -> None:
        trace_id = self.plugin.get_trace_id()
        if trace_id != "-":
            int(trace_id, 16)  # must be valid hex

    def test_setup_with_console_exporter(self) -> None:
        import os

        os.environ["OTEL_TRACES_EXPORTER"] = "console"
        self.plugin.setup("test-app")
        os.environ.pop("OTEL_TRACES_EXPORTER", None)
