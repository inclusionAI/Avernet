"""RunService（06-evolution-run.zh-CN §5、§6、§13）。"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import replace
from datetime import datetime
from typing import Any

from agentclaw.community.core.bot_genome.models import BotRef
from agentclaw.community.core.evolution_strategy import DEFAULT_CATALOG

from .budget import Budget, BudgetUsage
from .enums import EndReason, RunStatus
from .errors import (
    BindingBusy,
    IdempotencyKeyReused,
    RunAlreadyEnded,
    RunNotFound,
)
from .models_helpers import ALWAYS_GRANTED_PARTS
from .records import Job, Page, Run, RunCandidate, TriggerRecord
from .store import EvolutionStore, utcnow

__all__ = ["RunService"]


class RunService:
    def __init__(
        self,
        store: EvolutionStore,
        *,
        bindings,
        registry,
        genome,
        budget,
        kills,
        ledger=None,
    ) -> None:
        self.store = store
        self.bindings = bindings
        self.registry = registry
        self.genome = genome
        self.budget = budget
        self.kills = kills
        self.ledger = ledger

    async def submit(
        self,
        bot: BotRef,
        binding_id: str,
        *,
        idempotency_key: str,
        trigger: TriggerRecord,
        params: dict | None = None,
        budget: Budget | None = None,
    ) -> str:
        document = await self.bindings.get_bindings(bot)
        binding = document.find(binding_id)
        if binding is None:
            raise RunNotFound(f"binding {binding_id!r} does not exist on this bot")

        body_hash = _body_hash(binding_id, trigger, params, budget)
        existing = self.store.find_run_by_key(bot.owner_id, bot.bot_id, idempotency_key)
        if existing is not None:
            if (
                existing.idempotency_key == idempotency_key
                and existing.bot.bot_id == bot.bot_id
            ):
                recorded = self.store.conn.execute(
                    "SELECT body_hash FROM evolution_run WHERE run_id=?", (existing.id,)
                ).fetchone()
                if recorded is not None and recorded["body_hash"] != body_hash:
                    raise IdempotencyKeyReused(idempotency_key)
                return existing.id
            return existing.id

        # 绑定检查 + 紧急停止（§5.1 第 1 步）
        from .errors import BindingCheckFailed

        problems = await self.bindings.check(bot, [binding])
        if await self.kills.strategy_disabled(
            binding.strategy_id, binding.strategy_version
        ):
            problems.append(f"strategy {binding.strategy} is behind a kill switch")
        if await self.kills.bot_frozen(bot) or await self.kills.paused():
            problems.append("bot is frozen or the platform is paused")
        if problems:
            raise BindingCheckFailed(problems)

        # 绑定只有一个活跃运行（§4）
        _, active = self.store.list_runs(
            bot.owner_id,
            bot.bot_id,
            status=[RunStatus.QUEUED, RunStatus.RUNNING],
            binding_id=binding_id,
        )
        if active:
            raise BindingBusy(f"binding {binding_id} already has an active run")

        registered = await self.registry.get(
            binding.strategy_id, binding.strategy_version
        )
        run_id = _new_run_id()

        # 漂移检查 + 解析父版本（§5.1 第 2 步）
        drift = await self.genome.drift(bot)
        if drift is not None:
            run = self._build_run(
                bot,
                binding,
                run_id,
                idempotency_key,
                trigger,
                parent_revision="",
                params=_merged_params(binding, params),
                budget=_effective_budget(binding, budget),
                registered=registered,
                status=RunStatus.CANCELLED,
                end_reason=EndReason.OUT_OF_BAND_CHANGE,
                ended_at=utcnow(),
                cancel_requested=True,
            )
            self.store.insert_run(run, bot.owner_id, bot.bot_id, body_hash)
            return run.id

        parent = await self.genome.resolve_revision(bot, binding.parent)
        run = self._build_run(
            bot,
            binding,
            run_id,
            idempotency_key,
            trigger,
            parent_revision=parent.revision.id,
            params=_merged_params(binding, params),
            budget=_effective_budget(binding, budget),
            registered=registered,
        )
        await self.budget.reserve(bot, run.budget)
        self.store.insert_run(run, bot.owner_id, bot.bot_id, body_hash)
        self.store.insert_job(Job(id=f"{run_id}_1", run_id=run_id, attempt=1))
        if self.ledger is not None:
            await self.ledger.record_run(run)
        return run_id

    def _build_run(
        self,
        bot: BotRef,
        binding,
        run_id: str,
        key: str,
        trigger: TriggerRecord,
        *,
        parent_revision: str,
        params: dict,
        budget: Budget,
        registered: Any,
        status: RunStatus = RunStatus.QUEUED,
        end_reason: EndReason | None = None,
        ended_at: datetime | None = None,
        cancel_requested: bool = False,
    ) -> Run:
        registration = registered.registration
        definitions = {
            name: (ref.digest or "")
            for name, ref in registration.definition_refs().items()
        }
        granted = list(ALWAYS_GRANTED_PARTS)
        granted.extend(DEFAULT_CATALOG.granted_for(dict(registration.needs)))
        return Run(
            id=run_id,
            bot=bot,
            binding_id=binding.id,
            idempotency_key=key,
            trigger=trigger,
            strategy=binding.strategy_id,
            strategy_version=binding.strategy_version,
            parent_ref=binding.parent,
            parent_revision=parent_revision,
            allowed_genes=list(binding.allowed_genes),
            verification_profile=binding.verification_profile,
            params=params,
            budget_max={
                "max_usd": budget.max_usd,
                "max_wall_clock_s": budget.max_wall_clock_s,
                "max_rollouts": budget.max_rollouts,
                "max_tokens": budget.max_tokens,
            },
            granted=sorted(set(granted)),
            agent_definitions=definitions,
            status=status,
            attempt=1,
            max_attempts=3,
            budget_used=BudgetUsage(),
            candidate_count=0,
            created_at=utcnow(),
            ended_at=ended_at,
            end_reason=end_reason,
            cancel_requested=cancel_requested,
        )

    async def get(self, bot: BotRef, run_id: str) -> Run:
        run = self.store.get_run(run_id)
        if (
            run is None
            or run.bot.bot_id != bot.bot_id
            or run.bot.owner_id != bot.owner_id
        ):
            raise RunNotFound(run_id)
        return run

    async def list(
        self,
        bot: BotRef,
        *,
        status: list[RunStatus] | None = None,
        binding_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page:
        runs, total = self.store.list_runs(
            bot.owner_id,
            bot.bot_id,
            status=status,
            binding_id=binding_id,
            page=page,
            page_size=page_size,
        )
        return Page(items=runs, total=total, page=page, page_size=page_size)

    async def cancel(self, bot: BotRef, run_id: str, *, reason: str, actor: str) -> Run:
        run = await self.get(bot, run_id)
        if run.status is RunStatus.CANCELLED and run.cancel_requested:
            return run
        if run.terminal:
            raise RunAlreadyEnded(f"run {run_id} already ended as {run.status.value}")
        cancelled_ops = self.store.cancel_unfinished_operations(run_id)
        ended = replace(
            run,
            status=RunStatus.CANCELLED,
            cancel_requested=True,
            ended_at=utcnow(),
            end_reason=EndReason.CANCELLED_BY_OWNER,
            summary={
                "cancelled_operations": cancelled_ops,
                "reason": reason,
                "actor": actor,
            },
        )
        self.store.update_run(ended)
        return ended

    async def candidates(self, bot: BotRef, run_id: str) -> list[RunCandidate]:
        await self.get(bot, run_id)
        return self.store.candidates(run_id)


def _new_run_id() -> str:
    return "run_" + secrets.token_hex(6)


def _merged_params(binding, overrides: dict | None) -> dict[str, Any]:
    merged = dict(binding.params)
    if overrides:
        merged.update(overrides)
    return merged


def _effective_budget(binding, override: Budget | None) -> Budget:
    if override is None:
        return binding.budget
    if not override.within(binding.budget):
        from .errors import CeilingExceeded

        raise CeilingExceeded("run budget exceeds the binding's budget")
    return override


def _body_hash(
    binding_id: str, trigger: TriggerRecord, params: dict | None, budget: Budget | None
) -> str:
    material = repr(
        (
            binding_id,
            trigger.kind,
            trigger.fire_time,
            trigger.event_id,
            trigger.requested_by,
            params,
            budget,
        )
    )
    return hashlib.sha256(material.encode()).hexdigest()
