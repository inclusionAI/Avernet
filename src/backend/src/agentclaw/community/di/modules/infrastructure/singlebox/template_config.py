"""Profile-owned bootstrap for the local BaaS template routing map."""

from __future__ import annotations

from agentclaw.community.core.devices.services.baas_template_resolver import (
    BAAS_TEMPLATE_MAPPING_CATEGORY,
    BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY,
)
from agentclaw.community.core.system_config import SystemConfigService
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.utils import env_utils


logger = get_logger()

_LOCAL_TEMPLATE_UID = "local_default"
_SUPPORTED_ENGINES = ("openclaw", "moltis", "hermes", "aicoding", "claude_code")


class SingleboxBaasTemplateConfigLifecycle(LifecycleBase):
    """Install an idempotent BaaS template map after SQLite bootstrap."""

    def __init__(
        self,
        *,
        config_service: SystemConfigService,
        template_uuid: str | None,
    ) -> None:
        self._config_service = config_service
        self._template_uuid = template_uuid.strip() if template_uuid else ""

    async def startup(self) -> None:
        env = env_utils.get_current_env()
        existing = self._config_service.get_config(
            category=BAAS_TEMPLATE_MAPPING_CATEGORY,
            config_key=BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY,
            env=env,
        )
        if isinstance(existing, dict):
            return
        if not self._template_uuid.startswith("TEMPLATE-"):
            raise RuntimeError(
                "singlebox requires baas.template_uuid in TEMPLATE-* format"
            )

        self._config_service.create_category(
            category=BAAS_TEMPLATE_MAPPING_CATEGORY,
            category_name="System",
            description="Local BaaS system configuration",
            env=env,
            operator="local-bootstrap",
        )
        config_id = self._config_service.set_config(
            category=BAAS_TEMPLATE_MAPPING_CATEGORY,
            config_key=BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY,
            config_value={
                "version": "local-v1",
                "selectors": [
                    {"engine": engine, "template_uid": _LOCAL_TEMPLATE_UID}
                    for engine in _SUPPORTED_ENGINES
                ],
                "templates": {
                    _LOCAL_TEMPLATE_UID: {
                        "template_uuid": self._template_uuid,
                    }
                },
            },
            env=env,
            description="Generated from the local deployment configuration",
            operator="local-bootstrap",
        )
        if not config_id:
            raise RuntimeError("failed to seed local BaaS template mapping")
        logger.info(
            "Seeded local BaaS template mapping: env=%s template_uuid=%s",
            env,
            self._template_uuid,
        )
