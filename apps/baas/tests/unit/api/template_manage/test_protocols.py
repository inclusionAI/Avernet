"""Structural protocol conformance tests for DeviceTemplateManageService.

Verifies that DefaultDeviceTemplateService satisfies the DeviceTemplateManageService
Protocol (structural subtyping) and that the Protocol correctly defines
all expected methods.
"""

from typing import Protocol, get_type_hints

from secbaas.community.api.template_manage import (
    DeviceTemplateManageService,
    DeviceTemplateResponse,
    TemplateListResponse,
)
from secbaas.community.core.service.template_manage import DefaultDeviceTemplateService


class TestDeviceTemplateManageServiceProtocol:
    """Tests for the Protocol definition and structural conformance."""

    def test_is_protocol(self):
        """THEN DeviceTemplateManageService is a Protocol class."""
        assert issubclass(DeviceTemplateManageService, Protocol)

    def test_default_service_conforms(self):
        """THEN DefaultDeviceTemplateService structurally conforms to the Protocol.

        Uses runtime_checkable to verify via isinstance().
        """
        assert isinstance(DefaultDeviceTemplateService, DeviceTemplateManageService)

    def test_all_methods_defined(self):
        """THEN Protocol defines all expected methods."""
        expected = {
            "create_template",
            "get_by_template_id",
            "get_default_or_explicit_template",
            "get_online_template_by_uuid",
            "update_template",
            "update_status",
            "list_templates",
            "list_online_templates",
            "soft_delete_template",
        }
        protocol_methods = {
            name
            for name, value in DeviceTemplateManageService.__dict__.items()
            if not name.startswith("_")
        }
        assert protocol_methods == expected

    def test_method_signatures(self):
        """THEN each protocol method has correct return type hints."""
        hints = get_type_hints(DeviceTemplateManageService.create_template)
        assert hints["return"] == DeviceTemplateResponse

        hints = get_type_hints(DeviceTemplateManageService.get_by_template_id)
        assert hints["return"] == DeviceTemplateResponse | None

        hints = get_type_hints(
            DeviceTemplateManageService.get_default_or_explicit_template
        )
        assert hints["return"] == DeviceTemplateResponse

        hints = get_type_hints(DeviceTemplateManageService.get_online_template_by_uuid)
        assert hints["return"] == DeviceTemplateResponse | None

        hints = get_type_hints(DeviceTemplateManageService.update_template)
        assert hints["return"] == DeviceTemplateResponse | None

        hints = get_type_hints(DeviceTemplateManageService.update_status)
        assert hints["return"] == DeviceTemplateResponse | None

        hints = get_type_hints(DeviceTemplateManageService.list_templates)
        assert hints["return"] == TemplateListResponse

        hints = get_type_hints(DeviceTemplateManageService.list_online_templates)
        assert hints["return"] == TemplateListResponse

        hints = get_type_hints(DeviceTemplateManageService.soft_delete_template)
        assert hints["return"] is bool

    def test_default_service_has_all_methods(self):
        """THEN DefaultDeviceTemplateService defines all protocol methods."""
        for method_name in (
            "create_template",
            "get_by_template_id",
            "get_default_or_explicit_template",
            "get_online_template_by_uuid",
            "update_template",
            "update_status",
            "list_templates",
            "list_online_templates",
            "soft_delete_template",
        ):
            assert hasattr(DefaultDeviceTemplateService, method_name), (
                f"Missing method: {method_name}"
            )
