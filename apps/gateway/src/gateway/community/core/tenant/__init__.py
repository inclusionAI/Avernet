"""Tenant domain — canonical master-table ORM (``avernet_tenant``).

Holds the ORM row (:class:`TenantRow`). ORM-only for now; the eventual
tenant registry SPI/repository will live here when a consumer appears.
"""

from ._orm import TenantRow

__all__ = [
    "TenantRow",
]
