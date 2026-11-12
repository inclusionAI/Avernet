"""Task asset discovery; configuration storage is injected, never fetched over HTTP."""
from injector import inject
from agentclaw.community.core.errors import InternalError
from agentclaw.community.core.system_config.system_config_service_protocol import SystemConfigServiceProtocol
from .manifest_contract import (
    CATEGORY, KEY, PanelManifestConfig, TaskPanelManifest, TaskPanelManifestServiceProtocol,
)


class TaskPanelManifestService(TaskPanelManifestServiceProtocol):
    @inject
    def __init__(self, config: SystemConfigServiceProtocol) -> None:
        self._config = config

    def get_manifest(self, *, env: str) -> TaskPanelManifest:
        # Intentionally no process cache: a configuration change is visible on the
        # next discovery request, without restarting either host or this service.
        try:
            value = self._config.get_config(category=CATEGORY, config_key=KEY, env=env)
            config = PanelManifestConfig.from_value(value)
        except Exception as exc:
            raise InternalError("Task panel configuration is missing or unavailable") from exc
        return TaskPanelManifest(config.schema_version, env, config.bundles)
