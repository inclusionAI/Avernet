from unittest.mock import MagicMock

from secbaas.api.tenant_manage import (
    TenantManageService as TenantManageServiceProtocol,
)
from secbaas.core.repository.tenant import TenantRepository
from secbaas.core.service.tenant_manage import DefaultTenantManageService

# Assign value, will trigger mypy type check
_tenant_manage_service: TenantManageServiceProtocol = DefaultTenantManageService(
    tenant_repository=MagicMock(spec=TenantRepository),
)
