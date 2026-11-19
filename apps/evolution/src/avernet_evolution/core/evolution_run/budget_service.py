"""BudgetService（06-evolution-run.zh-CN §11、§13）。"""

from __future__ import annotations

from dataclasses import replace

from avernet_genome.models import BotRef

from .budget import Budget, BudgetUsage
from .enums import ChargeSource, RunStatus
from .errors import BudgetExhausted, CeilingExceeded
from .store import EvolutionStore, utcnow

__all__ = ["BudgetService"]


class BudgetService:
    def __init__(self, store: EvolutionStore, *, governance=None) -> None:
        self.store = store
        self.governance = governance

    async def reserve(self, bot: BotRef, budget: Budget) -> None:
        """§11.2：预留前检查 Bot 级与租户级上限。"""

        if self.governance is None:
            return
        for ceiling_name, getter in (
            ("bot", self.governance.bot_ceiling),
            ("tenant", self.governance.tenant_ceiling),
        ):
            ceiling = await getter(bot)
            if ceiling is not None and not budget.within(ceiling):
                raise CeilingExceeded(f"budget exceeds the {ceiling_name}-level ceiling")

    async def charge(self, run_id: str, usage: BudgetUsage, source: ChargeSource) -> BudgetUsage:
        used = BudgetUsage.of(**self.store.charge(run_id, usage, source))
        run = self.store.get_run(run_id)
        if run is None:
            return used
        exhausted = used.exhausted_dimension(run.budget)
        if exhausted is not None:
            ended = replace(
                run,
                status=RunStatus.BUDGET_EXHAUSTED,
                ended_at=utcnow(),
                end_reason=None,
                summary={"budget_exhausted": exhausted, "used": used.as_dict()},
            )
            self.store.update_run(ended)
            self.store.cancel_unfinished_operations(run_id)
            raise BudgetExhausted(f"run {run_id} exhausted its {exhausted} budget")
        updated = replace(run, budget_used=used)
        self.store.update_run(updated)
        return used

    async def remaining(self, run_id: str) -> BudgetUsage:
        return self.store.used_budget(run_id)
