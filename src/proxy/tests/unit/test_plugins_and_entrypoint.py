"""Unit tests for entrypoint, plugin accessor, and registry."""

from __future__ import annotations


class TestMain:
    def test_load_runner_bare(self) -> None:
        from sandboxproxy.community.main import _load_runner

        runner = _load_runner("bare")
        from sandboxproxy.community.plugins.runner.bare import BareAppRunnerPlugin

        assert isinstance(runner, BareAppRunnerPlugin)

    def test_load_runner_missing(self) -> None:
        import pytest

        from sandboxproxy.community.main import _load_runner

        with pytest.raises(RuntimeError, match="No runner registered"):
            _load_runner("does-not-exist")


class TestPluginAccessor:
    def test_fallback_used_in_bare_mode(self, monkeypatch) -> None:
        from sandboxproxy.community.plugin_accessor import PluginAccessor

        monkeypatch.setenv("SANDBOXPROXY_RUN_MODE", "bare")
        accessor = PluginAccessor("no.such.group", lambda: "fallback")
        assert accessor.get() == "fallback"

    def test_set_overrides(self) -> None:
        from sandboxproxy.community.plugin_accessor import PluginAccessor

        accessor = PluginAccessor("no.such.group", lambda: "fallback")
        accessor.set("injected")
        assert accessor.get() == "injected"


class TestPluginRegistry:
    def test_register_and_has(self) -> None:
        from sandboxproxy.community import plugin_registry

        plugin_registry.register_plugin_option("resolver", "extra", lambda: "x")
        assert plugin_registry.has_enterprise_plugins() is True

    def test_inject_into_container_noop(self) -> None:
        from sandboxproxy.community import plugin_registry

        class FakeContainer:
            def plugins(self):
                return _FakePlugins()

        plugin_registry.inject_into_plugin_container(FakeContainer())


class _FakePlugins:
    def __init__(self):
        self.resolver = None


class TestLoggerPlugin:
    def test_configure_and_get_logger(self) -> None:
        from sandboxproxy.community.plugins.logger.bare import BareLoggerPlugin

        plugin = BareLoggerPlugin()
        plugin.configure(log_level="DEBUG")
        logger = plugin.get_logger("test")
        assert logger is not None

    def test_logger_accessor(self) -> None:
        from sandboxproxy.community.logger import get_logger, get_logger_plugin

        assert get_logger_plugin() is not None
        assert get_logger("x") is not None


class TestTracerPlugin:
    def test_setup_and_install(self) -> None:
        from sandboxproxy.community.plugins.tracer.bare import BareTracerPlugin

        plugin = BareTracerPlugin()
        assert plugin.setup("app") is None
        assert plugin.install_middleware(object()) is None

    def test_tracer_accessor(self) -> None:
        from sandboxproxy.community.tracer import get_tracer_plugin

        assert get_tracer_plugin() is not None


class TestIdentity:
    def test_resolve_from_env(self, monkeypatch) -> None:
        from sandboxproxy.community.api.identity import resolve_instance_id

        monkeypatch.setenv("CONNECTED_SERVER_INSTANCE", "unit-1")
        assert resolve_instance_id() == "unit-1"

    def test_resolve_fallback(self, monkeypatch) -> None:
        from sandboxproxy.community.api.identity import (
            resolve_instance_id,
            resolve_worker_pid,
        )

        for key in ("CONNECTED_SERVER_INSTANCE", "INSTANCE_ID", "HOSTNAME"):
            monkeypatch.delenv(key, raising=False)
        assert resolve_instance_id()
        assert resolve_worker_pid() > 0


class TestRunnerPlugin:
    def test_run_sets_env(self, tmp_path, monkeypatch) -> None:
        from pathlib import Path

        import uvicorn

        from sandboxproxy.community.plugins.runner.bare import BareAppRunnerPlugin

        cfg = tmp_path / "application.yaml"
        cfg.write_text(
            "app_name: sandboxproxy\n"
            "user_config:\n"
            "  plugins:\n"
            "    resolver: stub\n"
            "    relay_client: stub\n"
            "  jwt:\n"
            "    secret: test\n"
        )

        captured: dict = {}

        def fake_run(**kwargs) -> None:
            captured.update(kwargs)

        monkeypatch.setattr(uvicorn, "run", fake_run)
        plugin = BareAppRunnerPlugin()
        try:
            plugin.run(config_path=str(cfg))
        finally:
            # ``plugin.run`` mutates the process environment directly (not via
            # monkeypatch); undo it immediately so later tests that load config
            # from the working directory are not redirected to a deleted temp
            # path.
            import os

            os.environ.pop("SANDBOXPROXY_RUN_MODE", None)
            os.environ.pop("SANDBOXPROXY_CONFIG_PATH", None)

        assert captured["app"] == "sandboxproxy.community.adapters.web.app:app"
