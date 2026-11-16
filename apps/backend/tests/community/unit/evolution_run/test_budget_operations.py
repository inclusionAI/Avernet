"""预算与长时操作（06 §8、§11）。"""

from __future__ import annotations

import pytest

from agentclaw.community.core.evolution_run import (
    Binding,
    Budget,
    BudgetExhausted,
    ChargeSource,
    ManualTrigger,
    OperationKind,
    OperationStatus,
    RunStatus,
    TriggerKind,
    TriggerRecord,
)
from agentclaw.community.core.evolution_run.budget import BudgetUsage

from .conftest import BOT

MANUAL = TriggerRecord(kind=TriggerKind.MANUAL, requested_by="user_5")


def binding(**overrides) -> Binding:
    values = {
        "id": "bind_01",
        "strategy": "acme/identity-hardening@1.0.0",
        "trigger": ManualTrigger(),
        "parent": "active",
        "allowed_genes": ["persona"],
        "verification_profile": "default@1",
        "budget": Budget(max_usd=1.0, max_wall_clock_s=3600),
        "params": {},
    }
    values.update(overrides)
    return Binding(**values)


async def _run(services, key="k"):
    await services.bindings.put_bindings(
        BOT, [binding()], expected_etag=None, actor="u"
    )
    return await services.runs.submit(
        BOT, "bind_01", idempotency_key=key, trigger=MANUAL
    )


@pytest.mark.asyncio
async def test_charge_accumulates_usage(services) -> None:
    run_id = await _run(services)
    await services.budget.charge(
        run_id, BudgetUsage.of(usd="0.25", tokens=100), ChargeSource.MODEL_CALL
    )
    used = await services.budget.remaining(run_id)
    assert used.usd == BudgetUsage.of(usd="0.25").usd and used.tokens == 100


@pytest.mark.asyncio
async def test_exhausting_budget_ends_the_run(services) -> None:
    run_id = await _run(services)
    with pytest.raises(BudgetExhausted):
        await services.budget.charge(
            run_id, BudgetUsage.of(usd="2.0"), ChargeSource.MODEL_CALL
        )
    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.BUDGET_EXHAUSTED


@pytest.mark.asyncio
async def test_operation_start_is_idempotent(services) -> None:
    run_id = await _run(services)
    first = await services.operations.start(
        run_id,
        OperationKind.TRAIN_EVALUATION,
        idempotency_key=f"{run_id}/score",
        request={},
    )
    second = await services.operations.start(
        run_id,
        OperationKind.TRAIN_EVALUATION,
        idempotency_key=f"{run_id}/score",
        request={},
    )
    assert first == second


@pytest.mark.asyncio
async def test_operation_result_charges_budget(services) -> None:
    run_id = await _run(services)
    op_id = await services.operations.start(
        run_id, OperationKind.AGENT_SESSION, idempotency_key=f"{run_id}/fix", request={}
    )
    op = await services.operations.record_result(
        run_id,
        op_id,
        status=OperationStatus.SUCCEEDED,
        result={"exit_status": "completed"},
        cost=BudgetUsage.of(usd="0.30", tokens=50),
    )
    assert op.status is OperationStatus.SUCCEEDED
    used = await services.budget.remaining(run_id)
    assert used.tokens == 50


@pytest.mark.asyncio
async def test_cancel_unfinished_counts(services) -> None:
    run_id = await _run(services)
    await services.operations.start(
        run_id, OperationKind.AGENT_SESSION, idempotency_key="a", request={}
    )
    await services.operations.start(
        run_id, OperationKind.AGENT_SESSION, idempotency_key="b", request={}
    )
    assert await services.operations.cancel_unfinished(run_id) == 2
