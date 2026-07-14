"""Tenant management service package — aligns with api/tenant_manage/."""

from ._tenant_manage_service import (
    DefaultTenantManageService,
    _record_to_response,
)

__all__ = [
    "DefaultTenantManageService",
    "_record_to_response",
]
