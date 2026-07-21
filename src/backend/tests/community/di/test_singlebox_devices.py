"""Singlebox device-connection projection tests."""

from unittest.mock import MagicMock

from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.core.devices.services.baas_template_resolver import (
    BaasTemplateResolution,
)
from agentclaw.community.di.modules.infrastructure.singlebox.devices import (
    SingleboxBaasDeviceService,
)


def _operator() -> OperatorContext:
    return OperatorContext(
        staff_id="u001",
        staff="u001",
        nick_name="User",
        operator_name="User",
    )


def _service(*, target: str) -> SingleboxBaasDeviceService:
    baas = MagicMock()
    baas._baas_api_base = "http://baas.local"
    baas.get_ws_info.return_value = MagicMock(
        target=target,
        token="connection-token",
        baas_base_url="http://baas.local",
        bot_uuid="BOT-1",
        tenant="team_claw",
        engine_port=20010,
    )
    bot_query = MagicMock()
    bot_query.get_by_binding_id.return_value = {
        "bot_id": "bot-1",
        "bot_type": "personal",
        "active_engine": "openclaw",
    }
    template_resolver = MagicMock()
    template_resolver.resolve_template.return_value = BaasTemplateResolution(
        template_uid="default_template",
        template_uuid="TEMPLATE-test",
        source="test",
    )
    return SingleboxBaasDeviceService(
        repository=MagicMock(),
        baas_service=baas,
        bot_query=bot_query,
        bot_sync=MagicMock(),
        oss_record_repo=MagicMock(),
        mcp_sync=MagicMock(),
        template_resolver=template_resolver,
    )


def test_singlebox_projects_loopback_baas_connection_as_local() -> None:
    service = _service(target="localhost:20010")

    connection = service.get_device_connection(binding_id=7, operator=_operator())

    assert connection.type == "local"
    assert connection.target == "localhost:20010"
    assert connection.token == "connection-token"


def test_singlebox_keeps_non_loopback_connection_as_baas() -> None:
    service = _service(target="engine.example.test:20010")

    connection = service.get_device_connection(binding_id=7, operator=_operator())

    assert connection.type == "baas"
