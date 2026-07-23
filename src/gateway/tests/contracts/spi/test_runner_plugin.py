import os
from unittest.mock import patch

from gateway.community.plugins.runner.bare._plugin import BareAppRunnerPlugin
from gateway.community.spi.runner import AppRunnerPlugin


class AppRunnerPluginContract:
    plugin: AppRunnerPlugin

    def test_run_accepts_config_path_none(self) -> None:
        with patch("uvicorn.run"):
            self.plugin.run()


class TestBareAppRunnerPlugin(AppRunnerPluginContract):
    def setup_method(self) -> None:
        self.plugin = BareAppRunnerPlugin()

    def test_run_sets_bare_mode_env(self) -> None:
        with patch("uvicorn.run"):
            self.plugin.run()
        assert os.environ.get("GATEWAY_RUN_MODE") == "bare"

    def test_run_with_config_path(self) -> None:
        with patch("uvicorn.run"):
            self.plugin.run(config_path="/tmp/test-config")
        assert os.environ.get("GATEWAY_CONFIG_PATH") == "/tmp/test-config"
