"""The OpenClaw port requests the native resolver, never configuration/history."""

from unittest.mock import AsyncMock

import pytest
from engine.community.kernel.frames import ResponseFrame
from engine.community.core.adapters.openclaw.models import OpenClawModelsAdapter
from .test_models_port import _impl


async def test_native_effective_value_is_used_verbatim():
    impl, client = _impl(
        {
            "models.default": ResponseFrame(
                id="1", ok=True, payload={"id": "p/inherited"}
            )
        }
    )
    result = await OpenClawModelsAdapter(impl).get_default_model()
    assert result.id == "p/inherited"
    assert client.calls == ["models.default"]


async def test_old_gateway_failure_is_not_catalogue_fallback():
    impl, client = _impl({"models.default": ResponseFrame(id="1", ok=False)})
    with pytest.raises(RuntimeError, match="query failed"):
        await OpenClawModelsAdapter(impl).get_default_model()
    assert client.calls == ["models.default"]


@pytest.mark.parametrize("payload", [{}, {"id": None}, {"id": ""}, {"id": "x\n"}])
async def test_invalid_native_reference_is_rejected(payload):
    port = AsyncMock()
    port.default_model.return_value = payload
    with pytest.raises(ValueError):
        await OpenClawModelsAdapter(port).get_default_model()
