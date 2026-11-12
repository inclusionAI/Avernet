"""Public task asset-discovery contract and configuration value types."""
from agentclaw.community.core.task.task_panel.manifest_contract import (
    PanelBundle, PanelManifestConfig, TaskPanelManifest,
    TaskPanelManifestServiceProtocol, is_panel_config,
)

__all__ = ["PanelBundle", "PanelManifestConfig", "TaskPanelManifest",
           "TaskPanelManifestServiceProtocol", "is_panel_config"]
