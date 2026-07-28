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
_TEMPLATE_UID_ALIASES = (_LOCAL_TEMPLATE_UID, "aicoding")
_SUPPORTED_ENGINES = ("openclaw", "moltis", "hermes", "aicoding", "claude_code")


class SingleboxBaasTemplateConfigLifecycle(LifecycleBase):
    """Install an idempotent BaaS template map after SQLite bootstrap."""

    def __init__(
        self,
        *,
        config_service: SystemConfigService,
        template_uuid: str,
    ) -> None:
        if not isinstance(template_uuid, str):
            raise TypeError("template_uuid must be str")
        self._config_service = config_service
        self._template_uuid = template_uuid.strip()

    async def startup(self) -> None:
        env = env_utils.get_current_env()
        existing = self._config_service.get_config(
            category=BAAS_TEMPLATE_MAPPING_CATEGORY,
            config_key=BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY,
            env=env,
        )
        if not self._template_uuid.startswith("TEMPLATE-"):
            raise RuntimeError(
                "singlebox requires baas.template_uuid in TEMPLATE-* format"
            )
        if isinstance(existing, dict):
            templates = existing.get("templates")
            if not isinstance(templates, dict):
                templates = {}
            missing_aliases = [
                alias for alias in _TEMPLATE_UID_ALIASES if alias not in templates
            ]
            if not missing_aliases:
                return
            updated = dict(existing)
            updated_templates = dict(templates)
            for alias in missing_aliases:
                updated_templates[alias] = {"template_uuid": self._template_uuid}
            updated["templates"] = updated_templates
            config_id = self._config_service.set_config(
                category=BAAS_TEMPLATE_MAPPING_CATEGORY,
                config_key=BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY,
                config_value=updated,
                env=env,
                description="Generated from the local deployment configuration",
                operator="local-bootstrap",
            )
            if not config_id:
                raise RuntimeError("failed to migrate local BaaS template mapping")
            logger.info(
                "Migrated local BaaS template mapping aliases: env=%s aliases=%s template_uuid=%s",
                env,
                missing_aliases,
                self._template_uuid,
            )
            return

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
                    alias: {
                        "template_uuid": self._template_uuid,
                    }
                    for alias in _TEMPLATE_UID_ALIASES
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
