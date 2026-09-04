import logging

from agentcompute.community.bootstrap import get_container
from agentcompute.community.plugins import register_plugins
from agentcompute.community.plugins.logger import StdlibLoggerPlugin
from agentcompute.community.plugins.tracer import StdlibTracerPlugin


def test_logger_plugin_returns_logger():
    plugin = StdlibLoggerPlugin()
    logger = plugin.get_logger("test.logger")
    assert isinstance(logger, logging.Logger)
    plugin.configure(log_level="DEBUG")


def test_logger_writes_to_log_dir(tmp_path):
    plugin = StdlibLoggerPlugin()
    plugin.configure(log_level="INFO", log_dir=str(tmp_path / "logs"))
    logger = plugin.get_logger("test.filelog")
    logger.info("file log entry")
    log_file = tmp_path / "logs" / "agentcompute.log"
    assert log_file.exists()
    assert "file log entry" in log_file.read_text(encoding="utf-8")


def test_tracer_plugin_default_trace_id():
    plugin = StdlibTracerPlugin()
    assert plugin.get_trace_id() == "-"
    plugin.setup("app")
    assert plugin._app_name == "app"


def test_logger_and_tracer_resolve_via_container():
    register_plugins()
    logger = get_container().plugins().logger()
    tracer = get_container().plugins().tracer()
    assert isinstance(logger, StdlibLoggerPlugin)
    assert isinstance(tracer, StdlibTracerPlugin)
