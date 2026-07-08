"""Tests for ``BaasService.resolve_container_provider`` — mapping a bot's
engine to a ``device_provider`` token.

A bot's container follows its engine: a ``teclaw`` engine runs in a teclaw
(pull-based external) container, everything else falls back to ``baas``. baas
is not queried for the bot's container in this path.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.plugins.local.http_client import LocalHttpClient


def _make_service() -> BaasService:
    return BaasService(
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
        http_client=LocalHttpClient(base_url="http://baas.test"),
        general_http_client=LocalHttpClient(base_url=""),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
    )


@pytest.mark.unit
def test_resolves_teclaw_when_engine_is_teclaw() -> None:
    svc = _make_service()
    assert svc.resolve_container_provider({"active_engine": "teclaw"}) == "teclaw"


@pytest.mark.unit
def test_engine_match_is_case_insensitive() -> None:
    svc = _make_service()
    assert svc.resolve_container_provider({"active_engine": "TeClaw"}) == "teclaw"


@pytest.mark.unit
def test_resolves_baas_when_engine_is_openclaw() -> None:
    svc = _make_service()
    assert svc.resolve_container_provider({"active_engine": "openclaw"}) == "baas"


@pytest.mark.unit
def test_missing_engine_falls_back_to_baas() -> None:
    svc = _make_service()
    assert svc.resolve_container_provider({"bot_id": "b"}) == "baas"


@pytest.mark.unit
def test_empty_bot_falls_back_to_baas() -> None:
    svc = _make_service()
    assert svc.resolve_container_provider({}) == "baas"


@pytest.mark.unit
def test_default_provider_is_overridable() -> None:
    svc = _make_service()
    assert (
        svc.resolve_container_provider(
            {"active_engine": "openclaw"}, default_provider="arca"
        )
        == "arca"
    )
