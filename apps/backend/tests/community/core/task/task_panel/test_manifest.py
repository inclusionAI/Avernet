from unittest.mock import Mock

import pytest

from agentclaw.community.core.errors import InternalError
from agentclaw.community.core.task.task_panel.manifest import (
    TaskPanelManifestService,
)

from agentclaw.community.core.task.task_panel.manifest_contract import PANEL_CONFIG_KEY, PanelManifestConfig


def configured(version="0.1.1"):
    return {"schema_version": 1, "bundles": [{"name": "taskPanel", "type": "url",
        "url": f"https://cdn.jsdelivr.net/npm/@avernet-assets/task-panel@{version}/dist/index.umd.js"}]}


def test_manifest_reads_current_environment_and_observes_updates_without_restart():
    store = Mock()
    store.get_config.return_value = configured()
    service = TaskPanelManifestService(store)
    first = service.get_manifest(env="pre")
    assert first.env == "pre" and first.schema_version == 1
    assert first.bundles[0].name == "taskPanel"
    store.get_config.assert_called_once_with(category="task", config_key="panel", env="pre")
    store.get_config.return_value = configured("0.1.2")
    assert "@0.1.2/" in service.get_manifest(env="pre").bundles[0].url
    service.get_manifest(env="prod")
    assert store.get_config.call_args.kwargs["env"] == "prod"
    store.set_config.assert_not_called()


@pytest.mark.parametrize("value", [None, {}, {"schema_version": True, "bundles": []}])
def test_missing_or_invalid_manifest_fails_closed(value):
    store = Mock()
    store.get_config.return_value = value
    with pytest.raises(InternalError):
        TaskPanelManifestService(store).get_manifest(env="pre")


def test_storage_failure_is_not_a_successful_empty_manifest():
    store = Mock()
    store.get_config.side_effect = RuntimeError("db unavailable")
    with pytest.raises(InternalError):
        TaskPanelManifestService(store).get_manifest(env="pre")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://example.test/index.umd.js",
    "https://user:secret@example.test/index.umd.js", "https://example.test/index.umd.js?q=secret",
    "https://example.test/index.umd.js#x", "https://cdn.jsdelivr.net/npm/@avernet-assets/task-panel@latest/dist/index.umd.js",
    "https://cdn.jsdelivr.net/npm/@avernet-assets/task-panel/dist/index.umd.js"])
def test_invalid_or_floating_asset_urls_are_rejected(url):
    value = configured()
    value["bundles"][0]["url"] = url
    with pytest.raises(ValueError):
        PanelManifestConfig.from_value(value)


def test_sensitive_config_key_aliases_cannot_bypass_the_write_guard():
    for category, key in [("task", "panel"), ("TASK ", "PANEL "), ("ｔａｓｋ", "ｐａｎｅｌ"), ("tásk", "panél")]:
        assert PANEL_CONFIG_KEY.matches(category, key)
    assert not PANEL_CONFIG_KEY.matches("task", "relay_execution_enabled")
