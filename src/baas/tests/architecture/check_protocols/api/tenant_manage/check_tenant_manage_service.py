from unittest.mock import MagicMock

from secbaas.community.api.tenant_manage import (
    TenantManageService as TenantManageServiceProtocol,
)
from secbaas.community.core.repository.tenant import TenantRepository
from secbaas.community.core.service.tenant_manage import DefaultTenantManageService

# Assign value, will trigger mypy type check
_tenant_manage_service: TenantManageServiceProtocol = DefaultTenantManageService(
    tenant_repository=MagicMock(spec=TenantRepository),
)
