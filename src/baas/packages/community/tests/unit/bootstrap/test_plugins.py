"""Tests for PluginContainer — standalone, no database needed."""

import pytest

from secbaas.bootstrap.plugins import PluginContainer


class TestPluginContainerStandalone:
    """PluginContainer can be created standalone with config injection."""

    def test_container_can_be_instantiated(self):
        """PluginContainer constructs without any arguments."""
        container = PluginContainer()
        assert container is not None

    def test_config_injection_via_from_dict(self):
        """Config from_dict flows to Selector providers."""
        container = PluginContainer()
        container.config.from_dict(
            {
                "plugins": {
                    "crypto": "stub",
                    "secret": "stub",
                    "permission": "stub",
                    "identity": "stub",
                    "scheduler": "stub",
                },
            }
        )
        assert container.config.plugins.crypto() == "stub"
