"""Port-impl tests for OpenClawPluginImpl.models_list / providers_list (transport).

Preserves the legacy engines/openclaw/tests/test_models.py transport coverage:
the models.list -> providers.list fallback (incl. the convert-empty case), the
provider flattening, and raw-dict returns. The dict->Model/Provider DTO build is
covered by core/adapters/openclaw/tests/test_models.py.
"""
from __future__ import annotations

from typing import Any

from engine.community.kernel.frames import ResponseFrame
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


class _FakeClient:
    """Returns canned ResponseFrames keyed by RPC method."""

    def __init__(self, responses: dict[str, ResponseFrame]):
        self.connected = True
        self._responses = responses
        self.calls: list[str] = []

    async def send_request(self, method: str, params: dict | None = None, timeout: Any = None):
        self.calls.append(method)
        if method not in self._responses:
            raise RuntimeError(f"unexpected RPC: {method}")
        return self._responses[method]


def _impl(responses) -> tuple[OpenClawPluginImpl, _FakeClient]:
    client = _FakeClient(responses)
    return OpenClawPluginImpl(client=client), client


async def test_models_list_returns_models_when_present():
    models = [{"id": "gpt-4"}, {"id": "claude"}]
    impl, client = _impl({"models.list": ResponseFrame(id="1", ok=True, payload={"models": models})})
    out = await impl.models_list()
    assert out == models
    assert "providers.list" not in client.calls  # no fallback when usable


async def test_models_list_falls_back_to_providers_when_empty():
    impl, client = _impl({
        "models.list": ResponseFrame(id="1", ok=True, payload={"models": []}),
        "providers.list": ResponseFrame(id="2", ok=True, payload={"providers": [
            {"id": "openai", "models": [{"id": "gpt-4"}]},
        ]}),
    })
    out = await impl.models_list()
    assert "providers.list" in client.calls
    assert any(e.get("id") == "gpt-4" and e.get("provider") == "openai" for e in out)


async def test_models_list_falls_back_when_entries_unconvertible():
    # Non-empty models.list but no entry has id/model -> must still fall back.
    impl, client = _impl({
        "models.list": ResponseFrame(id="1", ok=True, payload={"models": [{"desc": "x"}, {"meta": 1}]}),
        "providers.list": ResponseFrame(id="2", ok=True, payload={"providers": [
            {"id": "anthropic", "models": [{"id": "claude"}]},
        ]}),
    })
    out = await impl.models_list()
    assert "providers.list" in client.calls
    assert any(e.get("id") == "claude" for e in out)


async def test_providers_list_returns_raw_provider_dicts():
    providers = [{"id": "openai", "models": [{"id": "gpt-4"}]}]
    impl, _ = _impl({"providers.list": ResponseFrame(id="1", ok=True, payload={"providers": providers})})
    assert await impl.providers_list() == providers


async def test_providers_list_empty_on_not_ok():
    from engine.community.kernel.frames import ErrorShape
    impl, _ = _impl({"providers.list": ResponseFrame(id="1", ok=False, error=ErrorShape(code="X", message="e"))})
    assert await impl.providers_list() == []
