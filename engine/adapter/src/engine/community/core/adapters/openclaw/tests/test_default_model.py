"""Source-snapshot defaults must not be inferred from runtime/catalogue data."""
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
            port = SimpleNamespace(model_config_snapshot=AsyncMock(return_value=snapshot(
                {"agents": {"defaults": {"model": "provider/model"}}}
                if mode == "configured" else {}
            )))
            if mode == "failed":
                port.model_config_snapshot.side_effect = ConnectionError("unavailable")
            return OpenClawModelsAdapter(port)
        return factory


def snapshot(parsed):
    return {"exists": True, "valid": True, "parsed": parsed,
            "config": {"agents": {"defaults": {"model": "runtime/injected"}}}}


@pytest.mark.parametrize("value", ["provider/model", {"primary": "provider/model"}])
def test_reads_configured_value_without_normalizing(value):
    assert configured_default_model(snapshot({"agents": {"defaults": {"model": value}}})) == "provider/model"


@pytest.mark.parametrize("parsed", [{}, {"agents": {}}, {"agents": {"defaults": {}}},
    {"agents": {"defaults": {"model": {"fallbacks": ["p/m"]}}}}])
def test_missing_configuration_does_not_use_runtime_or_fallbacks(parsed):
    assert configured_default_model(snapshot(parsed)) is None


def test_missing_file_is_unconfigured():
    assert configured_default_model({"exists": False, "valid": True}) is None


@pytest.mark.parametrize("data", [{}, {"exists": True, "valid": False},
    {"exists": True, "valid": True, "parsed": None},
    snapshot({"agents": None}), snapshot({"agents": {"defaults": []}}),
    snapshot({"agents": {"defaults": {"model": 5}}}),
    snapshot({"agents": {"defaults": {"model": {"primary": None}}}}),
    snapshot({"agents": {"defaults": {"model": "  "}}})])
def test_invalid_snapshot_or_field_is_not_unconfigured(data):
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
        ok=True, payload=snapshot({"agents": {"defaults": {"model": "p/m"}}}),
    )))
    port._default_client = AsyncMock(return_value=client)
    assert await OpenClawModelsAdapter(port).get_default_model() == "p/m"
    client.send_request.assert_awaited_once_with("config.get", {}, timeout=5)
    client.send_request.return_value = SimpleNamespace(ok=False, payload={})
    with pytest.raises(RuntimeError):
        await OpenClawModelsAdapter(port).get_default_model()
