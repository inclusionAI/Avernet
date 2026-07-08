"""Tests for adapters/web/app.py — lifespan black-box verification.

Covers:
- app.py has no direct `secbaas.core.*` import for lifecycle wiring
- lifespan calls bootstrap entrypoints in correct order
- shutdown is executed even when exceptions occur
"""

import ast
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.adapters.web.app import app, lifespan, load_config
from secbaas.config import Config


class TestAppNoCoreImport:
    """Verify app.py does not import from secbaas.core for lifecycle wiring."""

    def test_no_core_import_in_app(self):
        """WHEN inspecting app.py source, THEN no `from secbaas.core` import exists.

        Note: ``secbaas.core.utils`` is exempt — cross-layer utility allowed
        for adapters/plugins per architecture layer rules.
        """
        source = inspect.getsource(
            __import__("secbaas.adapters.web.app", fromlist=["app"])
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(
                    "secbaas.core"
                ) or node.module.startswith("secbaas.core.utils"), (
                    f"Found forbidden import in app.py: from {node.module} import ..."
                )

    def test_app_has_correct_title(self):
        """Smoke: app title is set correctly."""
        assert app.title == "SecBaaS API"


class TestLifespanOrder:
    """Verify lifespan calls bootstrap entrypoints in correct order."""

    @pytest.fixture
    def app_mock(self):
        return MagicMock()

    @pytest.fixture
    def minimal_config(self):
        return Config(
            module_config={"zdas": {"enabled": False}},
            user_config={"arca": {"enabled": False}},
        )

    @pytest.mark.asyncio
    async def test_lifespan_calls_initialize_then_shutdown(
        self, app_mock, minimal_config
    ):
        """WHEN lifespan runs, THEN initialize_services is called before yield
        and shutdown_services is called after."""
        container = MagicMock(
            config=MagicMock(from_dict=MagicMock()),
            services=MagicMock(
                connection_management=MagicMock(
                    return_value=MagicMock(
                        ensure_initialized=MagicMock(), shutdown=AsyncMock()
                    )
                ),
                worker_router=MagicMock(
                    return_value=MagicMock(
                        start=AsyncMock(return_value="/tmp/test.sock"), stop=AsyncMock()
                    )
                ),
                instance_router=MagicMock(
                    return_value=MagicMock(ensure_initialized=MagicMock())
                ),
            ),
            init_resources=MagicMock(),
            cron_lifecycle=MagicMock(
                return_value=MagicMock(start=MagicMock(), stop=MagicMock())
            ),
        )
        call_order = []

        async def mock_initialize(c):
            call_order.append("initialize")

        async def mock_shutdown(c):
            call_order.append("shutdown")

        with (
            patch("secbaas.adapters.web.app.load_config", return_value=minimal_config),
            patch(
                "secbaas.adapters.web.app.ApplicationContainer", return_value=container
            ),
            patch(
                "secbaas.adapters.web.app.initialize_services",
                side_effect=mock_initialize,
            ),
            patch(
                "secbaas.adapters.web.app.shutdown_services", side_effect=mock_shutdown
            ),
        ):
            async with lifespan(app_mock):
                call_order.append("yield")

        assert call_order == ["initialize", "yield", "shutdown"]

    @pytest.mark.asyncio
    async def test_shutdown_called_on_exception(self, app_mock, minimal_config):
        """WHEN an exception occurs after yield, THEN shutdown_services is still called."""
        container = MagicMock(
            config=MagicMock(from_dict=MagicMock()),
            services=MagicMock(
                connection_management=MagicMock(
                    return_value=MagicMock(
                        ensure_initialized=MagicMock(), shutdown=AsyncMock()
                    )
                ),
                worker_router=MagicMock(
                    return_value=MagicMock(
                        start=AsyncMock(return_value="/tmp/test.sock"), stop=AsyncMock()
                    )
                ),
                instance_router=MagicMock(
                    return_value=MagicMock(ensure_initialized=MagicMock())
                ),
            ),
            init_resources=MagicMock(),
            cron_lifecycle=MagicMock(
                return_value=MagicMock(start=MagicMock(), stop=MagicMock())
            ),
        )
        shutdown_called = False

        async def mock_shutdown(c):
            nonlocal shutdown_called
            shutdown_called = True

        with (
            patch("secbaas.adapters.web.app.load_config", return_value=minimal_config),
            patch(
                "secbaas.adapters.web.app.ApplicationContainer", return_value=container
            ),
            patch("secbaas.adapters.web.app.initialize_services"),
            patch(
                "secbaas.adapters.web.app.shutdown_services", side_effect=mock_shutdown
            ),
        ):
            try:
                async with lifespan(app_mock):
                    raise RuntimeError("test error")
            except RuntimeError:
                pass

        assert shutdown_called, "shutdown_services was not called after exception"


class TestLoadConfig:
    """Tests for load_config() — sofapy standard config loading."""

    def test_loads_config_and_returns_config_object(self, tmp_path):
        """WHEN a valid YAML config file exists, THEN returns Config object."""
        mock_config = Config(
            app_name="test_app",
            user_config={"key": "value"},
        )
        with patch(
            "secbaas.adapters.web.app.ConfigLoader.load",
            return_value=mock_config,
        ):
            result = load_config()
            assert isinstance(result, Config)
            assert result.user_config["key"] == "value"
            assert result.app_name == "test_app"

    def test_config_not_found_raises_error(self):
        """WHEN config file not found, THEN get_config raises FileNotFoundError."""
        with patch(
            "secbaas.adapters.web.app.ConfigLoader.load",
            side_effect=FileNotFoundError("配置文件不存在"),
        ):
            with pytest.raises(FileNotFoundError, match="配置文件不存在"):
                load_config()

    def test_returns_config_with_sofapy_defaults(self):
        """WHEN sofapy Config is loaded, THEN Config object includes framework defaults."""
        mock_config = Config(
            app_name="sofapy_app",
            user_config={"arca": {"enabled": True}},
            workers=4,
        )
        with patch(
            "secbaas.adapters.web.app.ConfigLoader.load",
            return_value=mock_config,
        ):
            result = load_config()
            assert isinstance(result, Config)
            assert result.app_name == "sofapy_app"
            assert result.user_config["arca"]["enabled"] is True
            assert result.workers == 4
