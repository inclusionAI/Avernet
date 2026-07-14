"""Tests for PluginContainer — standalone, no database needed."""

from pathlib import Path

import pytest
import yaml

from secbaas.community.bootstrap.plugins import PluginContainer

COMMUNITY_DIR = Path(__file__).resolve().parents[3]
SINGLEBOX_DEV_CONFIG = COMMUNITY_DIR / "singlebox-configs" / "application-dev.yaml"


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

    def test_singlebox_dev_config_defines_required_selectors(self):
        """Singlebox dev config defines every selector resolved at startup."""
        raw_config = yaml.safe_load(SINGLEBOX_DEV_CONFIG.read_text())
        plugins = raw_config["user_config"]["plugins"]
        sandbox = plugins["sandbox"]

        assert plugins["auth"] == "stub"
        assert plugins["secret"] == "stub"
        assert plugins["cache"] == "stub"
        assert plugins["engine_adapter"] == "stub"
        assert plugins["bot_service"] == "local"
        assert plugins["database"]["plugin_database"] == "SQLITE_ORM"
        assert sandbox["arca"] == "local_proc"
        assert sandbox["desktop"] == "stub"
        assert sandbox["teclaw"] == "stub"
        assert sandbox["k8s"] == "stub"
        assert sandbox["docker"] == "stub"
        assert sandbox["poolab"] == "stub"
