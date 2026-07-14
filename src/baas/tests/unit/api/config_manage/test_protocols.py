"""Structural protocol conformance tests for SystemConfigManageService.

Verifies that the SystemConfigManageService Protocol correctly defines
all expected methods with proper signatures.
"""

from typing import Protocol, get_type_hints

from secbaas.community.api.config_manage import (
    SystemConfigListResponse,
    SystemConfigManageService,
    SystemConfigResponse,
)


class TestSystemConfigManageServiceProtocol:
    """Tests for the Protocol definition and structural conformance."""

    def test_is_protocol(self):
        """THEN SystemConfigManageService is a Protocol class."""
        assert issubclass(SystemConfigManageService, Protocol)

    def test_all_methods_defined(self):
        """THEN Protocol defines all expected methods."""
        expected = {
            "create_config",
            "get_config",
            "update_config",
            "delete_config",
            "list_configs",
        }
        protocol_methods = {
            name
            for name, value in SystemConfigManageService.__dict__.items()
            if not name.startswith("_")
        }
        assert protocol_methods == expected

    def test_method_signatures(self):
        """THEN each protocol method has correct return type hints."""
        hints = get_type_hints(SystemConfigManageService.create_config)
        assert hints["return"] == SystemConfigResponse

        hints = get_type_hints(SystemConfigManageService.get_config)
        assert hints["return"] == SystemConfigResponse | None

        hints = get_type_hints(SystemConfigManageService.update_config)
        assert hints["return"] == SystemConfigResponse | None

        hints = get_type_hints(SystemConfigManageService.delete_config)
        assert hints["return"] is bool

        hints = get_type_hints(SystemConfigManageService.list_configs)
        assert hints["return"] == SystemConfigListResponse
