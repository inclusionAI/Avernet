"""Public task asset-discovery contract and configuration value types."""
from agentclaw.community.core.task.task_panel.manifest_contract import (
    PanelBundle, PanelManifestConfig, TaskPanelManifest,
    TaskPanelManifestServiceProtocol,
)

__all__ = ["PanelBundle", "PanelManifestConfig", "TaskPanelManifest",
           "TaskPanelManifestServiceProtocol"]
