"""Conformance tests for ``BaasService.update_teclaw_outbound_rule_by_bot_uuid``.

The method's contract is **tri-state** (see ``BaasServiceProtocol``), because the
caller has to tell "there is nothing to write" from "there is something to write
but no device is ready for it yet":

- ``None`` — this provider mutates no egress at all (community/local).
- ``[]``   — a rule exists, but BaaS exposes no device with a
  ``provider_device_id`` yet (``start_device`` is what fills it).
- non-empty — every device of the bot was written.

Collapsing the middle state onto "success" is what let the create-time delivery
report a push that never happened (#527), so these exercise the concrete service
against the real HTTP shapes rather than a mocked protocol.
"""
from __future__ import annotations

from typing import Callable
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.kernel.device_dto import (
    HeaderOperationRule,
    OutBoundOperationRule,
)
from agentclaw.community.plugin_api.outbound_rules import OutboundRuleProvider
from agentclaw.community.plugins.local.http_client import LocalHttpClient
from agentclaw.community.plugins.local.outbound_rules import NoopOutboundRuleProvider


class _AgentPassRuleProvider(OutboundRuleProvider):
    """Stands in for the corp provider: a real agentpass rule to deliver."""

    def build_rule(
        self,
        *,
        bolt_id: str = "",
        device_id: str = "",
        owner_id: str = "",
        agent_pass_token: str = "",
        agent_code: str = "",
        bot_type_resolver: "Callable[[str, str], str | None] | None" = None,
    ) -> OutBoundOperationRule:
        return OutBoundOperationRule()

    def build_agentpass_rule(
        self,
        *,
        agent_pass_token: str = "",
    ) -> "OutBoundOperationRule | None":
        return OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["mcp.test"],
                    action="set",
                    header_name="x-agent-pass-token",
                    value=agent_pass_token,
                )
            ]
        )

    def build_caller_rule(
        self, *, caller_token: str
    ) -> "OutBoundOperationRule | None":
        return None


def _make_service(provider: OutboundRuleProvider) -> tuple[BaasService, LocalHttpClient]:
    http = LocalHttpClient(base_url="http://baas.test")
    service = BaasService(
        baas_api_base="http://baas.test",
        tenant="tnt",
        template_uuid="tpl",
        bot_repo=MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=MagicMock(),
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=http,
        general_http_client=LocalHttpClient(base_url=""),
        outbound_rule_provider=provider,
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
    )
    return service, http


def _devices_resp(items: list[dict]) -> MagicMock:
    """GET /bots/{uuid}/devices — data[0]['items'], as BaaS returns it."""
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {
        "code": 0,
        "message": "ok",
        "data": [{"items": items, "total": len(items), "page": 1, "page_size": 20}],
    }
    return r


def _ok_resp() -> MagicMock:
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"code": 0, "message": "ok", "data": {}}
    return r


@pytest.mark.unit
class TestUpdateTeclawOutboundRuleTriState:
    def test_provider_without_egress_mutation_returns_none_and_calls_nothing(self):
        # None is "nothing to deliver" — distinct from "not ready", and it must
        # not even reach BaaS.
        service, http = _make_service(NoopOutboundRuleProvider())

        assert service.update_teclaw_outbound_rule_by_bot_uuid(
            "BOT-x", agent_pass_token="tok"
        ) is None
        assert http.calls_to("get") == []
        assert http.calls_to("put") == []

    def test_no_devices_returns_empty_not_none(self):
        service, http = _make_service(_AgentPassRuleProvider())
        http.set_response("get", _devices_resp([]))

        assert service.update_teclaw_outbound_rule_by_bot_uuid(
            "BOT-x", agent_pass_token="tok"
        ) == []
        assert http.calls_to("put") == []

    def test_device_without_provider_device_id_is_not_ready(self):
        # The regression's exact shape: the row exists (create_bot wrote it) but
        # start_device has not filled provider_device_id yet.
        service, http = _make_service(_AgentPassRuleProvider())
        http.set_response(
            "get", _devices_resp([{"device_uuid": "DEVICE-1", "provider_device_id": None}])
        )

        assert service.update_teclaw_outbound_rule_by_bot_uuid(
            "BOT-x", agent_pass_token="tok"
        ) == []
        assert http.calls_to("put") == []

    def test_partially_ready_devices_write_nothing(self):
        # All-or-nothing: a partial write is neither retryable nor complete.
        service, http = _make_service(_AgentPassRuleProvider())
        http.set_response(
            "get",
            _devices_resp(
                [
                    {"device_uuid": "DEVICE-1", "provider_device_id": "TECLAW_1@4"},
                    {"device_uuid": "DEVICE-2", "provider_device_id": ""},
                ]
            ),
        )

        assert service.update_teclaw_outbound_rule_by_bot_uuid(
            "BOT-x", agent_pass_token="tok"
        ) == []
        assert http.calls_to("put") == []

    def test_all_ready_devices_are_written_and_reported(self):
        service, http = _make_service(_AgentPassRuleProvider())
        http.set_response(
            "get",
            _devices_resp(
                [
                    {"device_uuid": "DEVICE-1", "provider_device_id": "TECLAW_1@4"},
                    {"device_uuid": "DEVICE-2", "provider_device_id": "TECLAW_2@4"},
                ]
            ),
        )
        http.set_response("put", _ok_resp())

        updated = service.update_teclaw_outbound_rule_by_bot_uuid(
            "BOT-x", agent_pass_token="tok"
        )

        assert updated == [
            {"device_uuid": "DEVICE-1", "paas_device_id": "TECLAW_1@4"},
            {"device_uuid": "DEVICE-2", "paas_device_id": "TECLAW_2@4"},
        ]
        puts = http.calls_to("put")
        assert [call.args[0] for call in puts] == [
            "/api/v1/paas/devices/TECLAW_1@4/outbound-rule",
            "/api/v1/paas/devices/TECLAW_2@4/outbound-rule",
        ]
        # The token the caller handed in is what lands in the delivered rule.
        assert puts[0].kwargs["json"]["header_operation_rules"][0]["value"] == "tok"
