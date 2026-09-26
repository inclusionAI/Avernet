"""The consumer preserves platform responses and fails closed when unavailable."""
from unittest.mock import Mock
import pytest

from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeSettings, DigitalEmployeeServiceProtocol
from agentclaw.community.plugin_api.digital_employee import DigitalEmployeePlatformError
from agentclaw.community.plugins.community.digital_employee import UnavailableDigitalEmployeePlatform


def test_permission_query_returns_real_platform_progress(world, monkeypatch):
    service = world.get(DigitalEmployeeServiceProtocol)
    repository = Mock()
    repository.get_bot.return_value = {"ext": {"digital_employee": {"status": "ACTIVE", "work_no": "AI00000124"}}}
    platform = Mock()
    result = {"details": [{"mcpCode": "tools", "state": "APPLYING", "processUrl": ""}]}
    platform.query_mcp_permissions.return_value = result
    monkeypatch.setattr(service, "_settings", DigitalEmployeeSettings(enabled=True))
    monkeypatch.setattr(service, "_repo", repository)
    monkeypatch.setattr(service, "_platform", platform)
    assert service.query_permissions(17, ["tools"]) == result
    platform.query_mcp_permissions.assert_called_once_with("AI00000124", ["tools"])


def test_unavailable_platform_never_synthesizes_authorization(world, monkeypatch):
    service = world.get(DigitalEmployeeServiceProtocol)
    repository = Mock()
    repository.get_bot.return_value = {"ext": {"digital_employee": {"status": "ACTIVE", "work_no": "AI00000124"}}}
    platform = Mock(wraps=UnavailableDigitalEmployeePlatform())
    monkeypatch.setattr(service, "_settings", DigitalEmployeeSettings(enabled=True))
    monkeypatch.setattr(service, "_repo", repository)
    monkeypatch.setattr(service, "_platform", platform)
    with pytest.raises(DigitalEmployeePlatformError):
        service.query_permissions(17, ["tools"])
    platform.query_mcp_permissions.assert_called_once()


def test_catalog_and_publication_ports_are_fully_wired(world):
    from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeCatalogProtocol, DigitalEmployeePublicationProtocol
    assert callable(world.get(DigitalEmployeeCatalogProtocol).detail)
    assert callable(world.get(DigitalEmployeePublicationProtocol).capture_artifact)
