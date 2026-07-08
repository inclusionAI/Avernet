"""Tenant management exceptions.

Extracted from api/domain/tenant_manage.py as part of
api/domain → api/{domain_name}/ refactoring.
"""

from secbaas.api import DomainError


class TenantNotFoundError(DomainError):
    error_code = "TENANT_NOT_FOUND"
    http_status = 404

    def __init__(self, name: str = ""):
        self.name = name
        self.message = f"Tenant not found: {name}"
        super().__init__(self.message)
