"""OperationService（06-evolution-run.zh-CN §8、§13）。"""

from __future__ import annotations

import secrets
from typing import Any

from .budget import BudgetUsage
from .enums import ChargeSource, OperationKind, OperationStatus
from .errors import OperationNotFound
from .records import Operation
from .store import EvolutionStore, utcnow

__all__ = ["OperationService"]


class OperationService:
    def __init__(self, store: EvolutionStore, *, budget=None) -> None:
        self.store = store
        self.budget = budget

    async def start(
        self,
        run_id: str,
        kind: OperationKind,
        *,
        idempotency_key: str,
        request: dict[str, Any],
    ) -> str:
        """记录操作并交给执行器；返回操作 id（同键重入返回同一个）。"""

        operation = Operation(
            id="op_" + secrets.token_hex(4),
            run_id=run_id,
            kind=kind,
            idempotency_key=idempotency_key,
            status=OperationStatus.QUEUED,
            created_at=utcnow(),
        )
        return self.store.insert_operation(operation)

    async def get(self, run_id: str, operation_id: str) -> Operation:
        operation = self.store.get_operation(run_id, operation_id)
        if operation is None:
            raise OperationNotFound(operation_id)
        return operation

    async def cancel(self, run_id: str, operation_id: str) -> Operation:
        return self.store.cancel_operation(run_id, operation_id)

    async def cancel_unfinished(self, run_id: str) -> int:
        return self.store.cancel_unfinished_operations(run_id)

    async def record_result(
        self,
        run_id: str,
        operation_id: str,
        *,
        status: OperationStatus,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        cost: BudgetUsage | None = None,
    ) -> Operation:
        """执行器完成时回填结果；产生的成本立即计入运行预算（§8）。"""

        import json

        await self.get(run_id, operation_id)

        self.store.conn.execute(
            "UPDATE evolution_operation SET status=?, finished_at=?, result=?, error=?, cost=? "
            "WHERE operation_id=?",
            (
                status.value,
                utcnow().isoformat(),
                json.dumps(result) if result is not None else None,
                json.dumps(error) if error is not None else None,
                json.dumps(
                    {
                        "usd": str((cost or BudgetUsage()).usd),
                        "wall_clock_s": (cost or BudgetUsage()).wall_clock_s,
                        "rollouts": (cost or BudgetUsage()).rollouts,
                        "tokens": (cost or BudgetUsage()).tokens,
                    }
                ),
                operation_id,
            ),
        )
        self.store.conn.commit()
        if cost is not None and self.budget is not None:
            await self.budget.charge(run_id, cost, ChargeSource.OPERATION)
        return await self.get(run_id, operation_id)
