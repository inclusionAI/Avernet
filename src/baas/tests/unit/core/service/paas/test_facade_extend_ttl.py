"""Unit tests for PaasServiceFacade.extend_ttl method.

Covers the newly added fine-grained TTL extension path used by
DeadlineRenewalScheduler.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.template_manage import (
    ArcaTemplateConfig,
    TemplateStatus,
)
from secbaas.community.core.service.paas import (
    DeviceFacadeException,
    ErrorCode,
    PaasError,
    PaasServiceFacade,
)


@pytest.fixture
def facade():
    """Create a PaasServiceFacade with mocked dependencies."""
    mock_template_svc = MagicMock()
    mock_device_repo = MagicMock()
    mock_factory = MagicMock()
    return PaasServiceFacade(
        device_repository=mock_device_repo,
        device_template_service=mock_template_svc,
        factory=mock_factory,
    )


@pytest.fixture
def mock_service():
    """Create a mock PaasService with async extend_ttl."""
    mock = MagicMock()
    mock.extend_ttl = AsyncMock(return_value=True)
    mock.get_platform_type = AsyncMock(return_value="ARCA")
    return mock


def _make_arca_template():
    """Build a minimal ARCA template mock for facade lookups."""
    template = MagicMock()
    template.tenant = "test-tenant"
    template.template_uuid = "tpl-uuid-1"
    template.config = ArcaTemplateConfig(
        type="ARCA",
        base_url="https://arca.example.com",
        api_key="test-api-key",
        arca_template_id="tpl-1",
    )
    template.status = TemplateStatus.ONLINE
    return template


class TestExtendTtl:
    @pytest.mark.asyncio
    async def test_extend_ttl_success(self, facade, mock_service):
        """extend_ttl calls service.extend_ttl and returns True."""
        facade._device_template_service.get_by_template_id.return_value = (
            _make_arca_template()
        )
        facade._factory.create.return_value = mock_service

        result = await facade.extend_ttl(
            paas_device_id="sandbox-abc@42", ttl_minutes=720
        )

        assert result is True
        mock_service.extend_ttl.assert_awaited_once_with("sandbox-abc", 720)

    @pytest.mark.asyncio
    async def test_extend_ttl_template_not_found(self, facade):
        """extend_ttl raises DeviceFacadeException when template is missing."""
        facade._device_template_service.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc:
            await facade.extend_ttl(paas_device_id="sandbox-abc@99", ttl_minutes=720)

        assert exc.value.operation == "extend_ttl"

    @pytest.mark.asyncio
    async def test_extend_ttl_not_implemented(self, facade, mock_service):
        """extend_ttl wraps NotImplementedError as DeviceFacadeException."""
        mock_service.extend_ttl = AsyncMock(
            side_effect=NotImplementedError("not supported")
        )

        facade._device_template_service.get_by_template_id.return_value = (
            _make_arca_template()
        )
        facade._factory.create.return_value = mock_service

        with pytest.raises(DeviceFacadeException) as exc:
            await facade.extend_ttl(paas_device_id="sandbox-abc@42", ttl_minutes=720)

        assert exc.value.operation == "extend_ttl"

    @pytest.mark.asyncio
    async def test_extend_ttl_paas_error(self, facade, mock_service):
        """extend_ttl wraps PaasError as DeviceFacadeException."""
        mock_service.extend_ttl = AsyncMock(
            side_effect=PaasError(ErrorCode.PLATFORM_ERROR, "API failure")
        )

        facade._device_template_service.get_by_template_id.return_value = (
            _make_arca_template()
        )
        facade._factory.create.return_value = mock_service

        with pytest.raises(DeviceFacadeException) as exc:
            await facade.extend_ttl(paas_device_id="sandbox-abc@42", ttl_minutes=720)

        assert exc.value.operation == "extend_ttl"

    @pytest.mark.asyncio
    async def test_extend_ttl_without_template_suffix(self, facade, mock_service):
        """extend_ttl works with a paas_device_id that has no @template_id."""
        facade._device_template_service.get_by_template_id.return_value = (
            _make_arca_template()
        )
        facade._factory.create.return_value = mock_service

        result = await facade.extend_ttl(paas_device_id="sandbox-abc", ttl_minutes=720)

        assert result is True
        mock_service.extend_ttl.assert_awaited_once_with("sandbox-abc", 720)
