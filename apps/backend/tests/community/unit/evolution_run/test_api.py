"""09 公共 API 门面（传输无关）。"""

from __future__ import annotations

import pytest

from agentclaw.community.core.evolution_run import (
    BindingCheckFailed,
    EvolutionApi,
)

from .conftest import BOT


def _binding_doc() -> dict:
    return {
        "id": "bind_01",
        "strategy": "acme/identity-hardening@1.0.0",
        "trigger": {"manual": True},
        "parent": "active",
        "allowed_genes": ["persona"],
        "verification_profile": "default@1",
        "budget": {"max_usd": 5, "max_wall_clock_s": 3600},
        "params": {},
    }


@pytest.mark.asyncio
async def test_bindings_roundtrip(services) -> None:
    api = EvolutionApi(bindings=services.bindings, runs=services.runs)
    created = await api.put_bindings(
        BOT, [_binding_doc()], expected_etag=None, actor="u"
    )
    assert created["etag"]
    fetched = await api.get_bindings(BOT)
    assert fetched["bindings"][0]["id"] == "bind_01"


@pytest.mark.asyncio
async def test_submit_then_list_and_cancel(services) -> None:
    api = EvolutionApi(bindings=services.bindings, runs=services.runs)
    await api.put_bindings(BOT, [_binding_doc()], expected_etag=None, actor="u")
    submitted = await api.submit_run(
        BOT,
        {"binding_id": "bind_01", "idempotency_key": "k1", "trigger": {"manual": True}},
        actor="user_5",
    )
    run_id = submitted["run"]
    listed = await api.list_runs(BOT)
    assert listed["total"] == 1 and listed["runs"][0]["id"] == run_id
    cancelled = await api.cancel_run(BOT, run_id, reason="owner", actor="user_5")
    assert cancelled["run"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_error_envelope_maps_binding_failure(services) -> None:
    from agentclaw.community.core.evolution_run import error_envelope

    api = EvolutionApi(bindings=services.bindings, runs=services.runs)
    bad = _binding_doc()
    bad["allowed_genes"] = ["tools"]
    with pytest.raises(BindingCheckFailed) as raised:
        await api.put_bindings(BOT, [bad], expected_etag=None, actor="u")
    envelope = error_envelope(raised.value)
    assert envelope["status"] == 422
    assert envelope["body"]["error"]["code"] == "binding_check_failed"
