"""Structural protocol conformance tests for TenantManageService.

Verifies that DefaultTenantManageService satisfies the TenantManageService
Protocol (structural subtyping) and that the Protocol correctly defines
all expected methods.
"""

from typing import Protocol, get_type_hints

from secbaas.community.api.tenant_manage import (
    TenantConfig,
    TenantListResponse,
    TenantManageService,
    TenantResponse,
)
from secbaas.community.core.service.tenant_manage import DefaultTenantManageService


class TestTenantManageServiceProtocol:
    """Tests for the Protocol definition and structural conformance."""

    def test_is_protocol(self):
        """THEN TenantManageService is a Protocol class."""
        assert issubclass(TenantManageService, Protocol)

    def test_default_service_conforms(self):
        """THEN DefaultTenantManageService structurally conforms to the Protocol.

        Uses runtime_checkable to verify via isinstance(). Note: this passes
        because DefaultTenantManageService defines all methods with matching
        signatures. Since methods are @staticmethod, we check the class itself.
        """
        # isinstance with runtime_checkable Protocol checks method presence
        assert isinstance(DefaultTenantManageService, TenantManageService)

    def test_all_methods_defined(self):
        """THEN Protocol defines all expected methods."""
        expected = {
            "create_tenant",
            "get_tenant_by_name",
            "get_tenant_config",
            "update_tenant",
            "list_tenants",
            "soft_delete_tenant",
        }
        protocol_methods = {
            name
            for name, value in TenantManageService.__dict__.items()
            if not name.startswith("_")
        }
        assert protocol_methods == expected

    def test_method_signatures(self):
        """THEN each protocol method has correct return type hints."""
        hints = get_type_hints(TenantManageService.create_tenant)
        assert hints["return"] == TenantResponse

        hints = get_type_hints(TenantManageService.get_tenant_by_name)
        assert hints["return"] == TenantResponse | None

        hints = get_type_hints(TenantManageService.get_tenant_config)
        assert hints["return"] == TenantConfig | None

        hints = get_type_hints(TenantManageService.update_tenant)
        assert hints["return"] == TenantResponse | None

        hints = get_type_hints(TenantManageService.list_tenants)
        assert hints["return"] == TenantListResponse

        hints = get_type_hints(TenantManageService.soft_delete_tenant)
        assert hints["return"] is bool

    def test_default_service_has_all_methods(self):
        """THEN DefaultTenantManageService defines all protocol methods."""
        for method_name in (
            "create_tenant",
            "get_tenant_by_name",
            "get_tenant_config",
            "update_tenant",
            "list_tenants",
            "soft_delete_tenant",
        ):
            assert hasattr(DefaultTenantManageService, method_name), (
                f"Missing method: {method_name}"
            )
