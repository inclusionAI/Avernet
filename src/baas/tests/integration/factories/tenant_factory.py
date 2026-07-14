"""Factory for tenant and template entities."""

import random
from typing import Any

from .base import DEFAULT_TEST_CREATOR, DEFAULT_TEST_MODIFIER


class TenantFactory:
    """Build tenant and template records with minimal boilerplate."""

    def __init__(self, tenant_repo: Any, env: str) -> None:
        self.tenant_repo = tenant_repo
        self.env = env

    def create_tenant(self, name: str = "test_tenant", **overrides: Any) -> int:
        """Create a tenant record and return its ID."""
        defaults = {
            "name": name,
            "env": self.env,
            "creator": DEFAULT_TEST_CREATOR,
            "modifier": DEFAULT_TEST_MODIFIER,
            "description": None,
            "extra_config": None,
        }
        defaults.update(overrides)
        return self.tenant_repo.insert_tenant(**defaults)

    def create_tenant_with_template(
        self, name: str = "test_tenant", **overrides: Any
    ) -> tuple[str, int]:
        """Create a tenant and a device template, return ``(tenant_name, template_id)``.

        Args:
            name: Tenant name.
            **overrides: Optional overrides keyed by entity type::
                - ``tenant``: dict forwarded to ``create_tenant``
                - ``template``: dict forwarded to ``DeviceTemplateService.create_template``

        Returns:
            ``(tenant_name, template_id)``
        """
        from secbaas.community.api.template_manage import (
            ArcaTemplateConfig,
            TemplateCreate,
        )
        from secbaas.community.api.tenant_manage import TenantType
        from secbaas.community.core.service.template_manage import (
            DefaultDeviceTemplateService as DeviceTemplateService,
        )

        tenant_ov = overrides.pop("tenant", {})
        template_ov = overrides.pop("template", {})

        # Re-use existing tenant if present
        existing = self.tenant_repo.get_by_name(name, self.env)
        if existing:
            tenant_name = existing.name
        else:
            self.create_tenant(name=name, **tenant_ov)
            tenant_name = name

        template_uuid = template_ov.pop("template_uuid", f"tpl-test-{name}")
        template_name = template_ov.pop("name", f"Test Template {template_uuid}")
        config = template_ov.pop(
            "config",
            ArcaTemplateConfig(
                type="ARCA",
                base_url="http://test",
                api_key="test",
                template_id="ARCA-TEMPLATE-test",
                arca_template_id_pre=None,
                arca_template_id_prod=None,
                oss_mount_id=None,
            ),
        )

        template = DeviceTemplateService.create_template(
            tenant=tenant_name,
            data=TemplateCreate(
                template_uuid=template_uuid,
                template_id=random.randint(1, 999999999),
                type=TenantType.ARCA,
                name=template_name,
                config=config,
                operator=DEFAULT_TEST_CREATOR,
                **template_ov,
            ),
        )
        return tenant_name, template.id
