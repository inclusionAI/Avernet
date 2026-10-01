"""Tests for agentclaw.community.di.container module.

Covers:
- get_app_injector() behavior when not initialized
- get_app_injector() behavior after build_injector() is called
"""
from __future__ import annotations

import pytest

from agentclaw.community.di.container import _app_injector, get_app_injector


class TestGetAppInjector:
    """Tests for get_app_injector function."""

    def test_raises_runtime_error_when_not_initialized(self):
        """get_app_injector should raise RuntimeError if injector not built."""
        # Reset global state before test
        import agentclaw.community.di.container as container_module
        original_injector = container_module._app_injector
        container_module._app_injector = None

        try:
            with pytest.raises(RuntimeError) as exc_info:
                get_app_injector()

            assert "DI injector not initialized" in str(exc_info.value)
            assert "build_injector()" in str(exc_info.value)
        finally:
            # Restore original state
            container_module._app_injector = original_injector

    def test_returns_injector_after_build(self, test_injector):
        """get_app_injector should return the injector after build_injector is called."""
        import agentclaw.community.di.container as container_module

        # The test_injector fixture builds an injector, but it doesn't set
        # the global _app_injector. We need to simulate build_injector behavior.
        original_injector = container_module._app_injector

        try:
            # Set the global injector (simulating build_injector behavior)
            container_module._app_injector = test_injector

            result = get_app_injector()

            assert result is test_injector
        finally:
            # Restore original state
            container_module._app_injector = original_injector