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
    # ``plugin.run`` mutates os.environ (GATEWAY_RUN_MODE / GATEWAY_CONFIG_PATH);
    # snapshot and restore so the change does not leak into later tests (e.g. the
    # config-driven app factory, which reads GATEWAY_CONFIG_PATH).
    _LEAKED_ENV = ("GATEWAY_RUN_MODE", "GATEWAY_CONFIG_PATH")

    def setup_method(self) -> None:
        self.plugin = BareAppRunnerPlugin()
        self._env_snapshot = {k: os.environ.get(k) for k in self._LEAKED_ENV}

    def teardown_method(self) -> None:
        for key, value in self._env_snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_run_sets_bare_mode_env(self) -> None:
        with patch("uvicorn.run"):
            self.plugin.run()
        assert os.environ.get("GATEWAY_RUN_MODE") == "bare"

    def test_run_with_config_path(self) -> None:
        with patch("uvicorn.run"):
            self.plugin.run(config_path="/tmp/test-config")
        assert os.environ.get("GATEWAY_CONFIG_PATH") == "/tmp/test-config"
