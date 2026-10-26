"""Configured defaults must not be inferred from runtime/catalogue data."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from engine.community.core.adapters.openclaw.default_model import configured_default_model
from engine.community.core.adapters.openclaw.models import OpenClawModelsAdapter
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.plugins.openclaw._models import _ModelsPortMixin
from engine.community.core.models.tests.default_model_contract import DefaultModelContract


class TestOpenClawDefaultModelContract(DefaultModelContract):
    @pytest.fixture
    def default_model_factory(self):
        def factory(mode):
            port = SimpleNamespace(get_model_config=AsyncMock(return_value=config_response(
                {"agents": {"defaults": {"model": "provider/model"}}}
                if mode == "configured" else {}
            )))
            if mode == "failed":
                port.get_model_config.side_effect = ConnectionError("unavailable")
            return OpenClawModelsAdapter(port)
        return factory


def config_response(parsed):
    return {"exists": True, "valid": True, "parsed": parsed,
            "config": {"agents": {"defaults": {"model": "runtime/injected"}}}}


@pytest.mark.parametrize("value", ["provider/model", {"primary": "provider/model"}])
def test_reads_configured_value_without_normalizing(value):
    assert configured_default_model(config_response({"agents": {"defaults": {"model": value}}})) == "provider/model"


@pytest.mark.parametrize("parsed", [{}, {"agents": {}}, {"agents": {"defaults": {}}},
    {"agents": {"defaults": {"model": {"fallbacks": ["p/m"]}}}}])
def test_missing_configuration_does_not_use_runtime_or_fallbacks(parsed):
    assert configured_default_model(config_response(parsed)) is None


def test_missing_file_is_unconfigured():
    assert configured_default_model({"exists": False, "valid": True}) is None


@pytest.mark.parametrize("data", [{}, {"exists": True, "valid": False},
    {"exists": True, "valid": True, "parsed": None},
    config_response({"agents": None}), config_response({"agents": {"defaults": []}}),
    config_response({"agents": {"defaults": {"model": 5}}}),
    config_response({"agents": {"defaults": {"model": {"primary": None}}}}),
    config_response({"agents": {"defaults": {"model": "  "}}})])
def test_invalid_config_response_or_field_is_not_unconfigured(data):
    with pytest.raises(ValueError):
        configured_default_model(data)


async def test_old_catalogue_port_still_works_without_default_extension():
    port = SimpleNamespace(models_list=AsyncMock(return_value=[]))
    adapter = OpenClawModelsAdapter(port)
    assert await adapter.list_models() == []
    with pytest.raises(CapabilityNotSupportedError):
        await adapter.get_default_model()


async def test_live_port_uses_existing_config_get_and_preserves_read_failure():
    port = _ModelsPortMixin()
    client = SimpleNamespace(send_request=AsyncMock(return_value=SimpleNamespace(
        ok=True, payload=config_response({"agents": {"defaults": {"model": "p/m"}}}),
    )))
    port._default_client = AsyncMock(return_value=client)
    assert await OpenClawModelsAdapter(port).get_default_model() == "p/m"
    client.send_request.assert_awaited_once_with("config.get", {}, timeout=5)
    client.send_request.return_value = SimpleNamespace(ok=False, payload={})
    with pytest.raises(RuntimeError):
        await OpenClawModelsAdapter(port).get_default_model()


async def test_config_is_read_again_on_each_request():
    port = _ModelsPortMixin()
    client = SimpleNamespace(send_request=AsyncMock(side_effect=[
        SimpleNamespace(ok=True, payload=config_response({"agents": {"defaults": {"model": model}}}))
        for model in ("provider/first", "provider/updated")
    ]))
    port._default_client = AsyncMock(return_value=client)
    adapter = OpenClawModelsAdapter(port)
    assert await adapter.get_default_model() == "provider/first"
    assert await adapter.get_default_model() == "provider/updated"
    assert client.send_request.await_count == 2


@pytest.mark.parametrize("error", [ConnectionError("unavailable"), TimeoutError("deadline")])
async def test_config_rpc_failure_propagates_without_substituting_a_value(error):
    port = _ModelsPortMixin()
    client = SimpleNamespace(send_request=AsyncMock(side_effect=error))
    port._default_client = AsyncMock(return_value=client)
    with pytest.raises(type(error)):
        await OpenClawModelsAdapter(port).get_default_model()
    client.send_request.assert_awaited_once_with("config.get", {}, timeout=5)
