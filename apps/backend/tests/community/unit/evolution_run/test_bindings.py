"""绑定检查（06 §3.2）。"""

from __future__ import annotations

import pytest

from agentclaw.community.core.evolution_run import (
    Binding,
    BindingCheckFailed,
    Budget,
    ManualTrigger,
    PreconditionFailed,
    ScheduleTrigger,
)

from .conftest import BOT


def binding(**overrides) -> Binding:
    values = {
        "id": "bind_01",
        "strategy": "acme/identity-hardening@1.0.0",
        "trigger": ManualTrigger(),
        "parent": "active",
        "allowed_genes": ["persona"],
        "verification_profile": "default@1",
        "budget": Budget(max_usd=5.0, max_wall_clock_s=3600),
        "params": {},
    }
    values.update(overrides)
    return Binding(**values)


@pytest.mark.asyncio
async def test_put_and_get_bindings(services) -> None:
    stored = await services.bindings.put_bindings(
        BOT, [binding()], expected_etag=None, actor="u"
    )
    assert stored.etag
    fetched = await services.bindings.get_bindings(BOT)
    assert [b.id for b in fetched.bindings] == ["bind_01"]
    assert fetched.etag == stored.etag


@pytest.mark.asyncio
async def test_create_conflicts_when_document_exists(services) -> None:
    await services.bindings.put_bindings(
        BOT, [binding()], expected_etag=None, actor="u"
    )
    with pytest.raises(PreconditionFailed):
        await services.bindings.put_bindings(
            BOT, [binding()], expected_etag=None, actor="u"
        )


@pytest.mark.asyncio
async def test_etag_mismatch_is_rejected(services) -> None:
    await services.bindings.put_bindings(
        BOT, [binding()], expected_etag=None, actor="u"
    )
    with pytest.raises(PreconditionFailed):
        await services.bindings.put_bindings(
            BOT, [binding()], expected_etag="wrong", actor="u"
        )


@pytest.mark.asyncio
async def test_gene_outside_policy_is_rejected(services) -> None:
    with pytest.raises(BindingCheckFailed) as raised:
        await services.bindings.put_bindings(
            BOT, [binding(allowed_genes=["tools"])], expected_etag=None, actor="u"
        )
    assert any("mutable_genes" in problem for problem in raised.value.problems)


@pytest.mark.asyncio
async def test_unregistered_strategy_is_rejected(services) -> None:
    with pytest.raises(BindingCheckFailed) as raised:
        await services.bindings.put_bindings(
            BOT, [binding(strategy="nobody/none@9.9.9")], expected_etag=None, actor="u"
        )
    assert any("not registered" in problem for problem in raised.value.problems)


@pytest.mark.asyncio
async def test_invalid_cron_is_rejected(services) -> None:
    with pytest.raises(BindingCheckFailed) as raised:
        await services.bindings.put_bindings(
            BOT,
            [binding(trigger=ScheduleTrigger("0 2 * *"))],
            expected_etag=None,
            actor="u",
        )
    assert any("five fields" in problem for problem in raised.value.problems)


@pytest.mark.asyncio
async def test_budget_above_tenant_ceiling_is_rejected(services) -> None:
    services.governance.tenant_ceiling_budget = Budget(max_usd=1.0, max_wall_clock_s=60)
    with pytest.raises(BindingCheckFailed) as raised:
        await services.bindings.put_bindings(
            BOT, [binding()], expected_etag=None, actor="u"
        )
    assert any("tenant-level ceiling" in problem for problem in raised.value.problems)
