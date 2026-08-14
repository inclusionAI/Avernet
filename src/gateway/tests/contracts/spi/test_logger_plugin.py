import logging

from gateway.community.plugins.logger.bare._plugin import BareLoggerPlugin
from gateway.community.spi.logger import LoggerPlugin


class LoggerPluginContract:
    plugin: LoggerPlugin

    def test_get_logger_returns_logger(self) -> None:
        logger = self.plugin.get_logger("test-logger")
        assert isinstance(logger, logging.Logger)

    def test_get_logger_default_name(self) -> None:
        logger = self.plugin.get_logger()
        assert isinstance(logger, logging.Logger)

    def test_get_logger_same_name_returns_same_instance(self) -> None:
        a = self.plugin.get_logger("test-same")
        b = self.plugin.get_logger("test-same")
        assert a is b

    def test_configure_sets_level(self) -> None:
        self.plugin.configure(log_level="DEBUG")
        logger = self.plugin.get_logger("test-configured")
        assert logger.level == logging.DEBUG

    def test_configure_creates_log_dir(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            self.plugin.configure(log_dir=tmpdir, app_name="test-app")
            assert Path(tmpdir).is_dir()


class TestBareLoggerPlugin(LoggerPluginContract):
    def setup_method(self) -> None:
        self.plugin = BareLoggerPlugin()

    def test_get_logger_caches_by_name(self) -> None:
        _ = self.plugin.get_logger("test-cache")
        assert "test-cache" in self.plugin._loggers

    def test_configure_unknown_level_falls_back_to_info(self) -> None:
        self.plugin.configure(log_level="INVALID_LEVEL")
        logger = self.plugin.get_logger("test-fallback")
        assert logger.level == logging.INFO

    def test_configure_numeric_level(self) -> None:
        self.plugin.configure(log_level="10")
        logger = self.plugin.get_logger("test-numeric")
        assert logger.level == logging.DEBUG
