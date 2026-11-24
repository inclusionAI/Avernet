"""Independent composition root for 01–06, plus the 07 verifier when the platform provides one.

Evaluation (07) and promotion (08) are optional ports: a deployment that omits them keeps
the 01–06 behaviour, where every candidate stays `pending`.
"""

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from .config import Settings
from .core.evolution_ledger import LocalExperimentLedger
from .core.evolution_ledger.models import VerdictRecord
from .core.evolution_ledger.models import VerdictStatus as LedgerVerdictStatus
from .core.evolution_run.api import EvolutionApi
from .core.evolution_run.bindings_service import BindingsService
from .core.evolution_run.budget_service import BudgetService
from .core.evolution_run.container_host import ContainerHost
from .core.evolution_run.context import build_run_context
from .core.evolution_run.host import InProcessHost
from .core.evolution_run.host_pool import HostPool
from .core.evolution_run.job_protocol import JobChannel, JobTokenIssuer
from .core.evolution_run.job_service import JobService
from .core.evolution_run.operation_service import OperationService
from .core.evolution_run.run_service import RunService
from .core.evolution_run.runner import EvolutionRunner
from .core.evolution_run.scheduler import TriggerScheduler
from .core.evolution_run.store import EvolutionStore
from .core.evolution_verify.enums import Split
from .identity import validate_identities
from .plugin_api import Platform


def _compose_final(*callbacks: Any):
    """把多个"判定落定"消费者按顺序串起来，任一失败不影响后续。"""

    async def run(verdict: Any) -> None:
        for callback in callbacks:
            try:
                await callback(verdict)
            except Exception:  # noqa: BLE001 - 证据/门禁失败不能反噬判定本身
                import logging

                logging.getLogger(__name__).warning(
                    "evolution.final_callback_failed callback=%s", getattr(callback, "__name__", callback)
                )

    return run


def _strategy_aggregates(verdict: Any) -> dict[str, Any]:
    """只回传 validation 的聚合值；逐用例细节留在评估记录里。"""

    validation = verdict.splits.get(Split.VALIDATION) if hasattr(verdict, "splits") else None
    if validation is None or validation.ci is None or validation.mean_delta is None:
        return {}
    return {
        "validation": SimpleNamespace(
            cases=validation.cases,
            seeds=validation.seeds,
            mean_delta=validation.mean_delta,
            ci=tuple(validation.ci),
        )
    }


@dataclass
class Runtime:
    store: EvolutionStore
    ledger: LocalExperimentLedger
    api: EvolutionApi
    channel: JobChannel
    runner: EvolutionRunner
    platform: Platform

    async def close(self):
        try:
            await self.runner.shutdown()
        finally:
            try:
                await self.platform.close()
            finally:
                self.ledger.conn.close()
                self.store.conn.close()


async def compose(settings: Settings, platform: Platform) -> Runtime:
    if settings.profile == "production" and (platform.sandbox is None or not platform.worker_image):
        raise ValueError("production requires an isolated sandbox and pinned worker image")
    settings.signing_key()
    image_groups: dict[str, list[str]] = {}
    if platform.sandbox is not None:
        from .core.evolution_run.sandbox import job_worker_spec

        for record in await platform.registry.list():
            registration = record.registration
            ref = f"{registration.id}@{registration.version}"
            if registration.runtime.kind != "job_worker" or ref not in platform.strategies:
                raise ValueError("isolated strategy requires a registered Worker image and factory")
            image = registration.runtime.image
            if not image:
                raise ValueError("registered Worker image is required")
            job_worker_spec(
                worker_id="config-check",
                image=image,
                job_endpoint=settings.job_endpoint,
                job_token="validation",
            )
            image_groups.setdefault(image, []).append(ref)
        if not image_groups:
            raise ValueError("isolated deployment has no Worker strategies")
    store = EvolutionStore(settings.database)
    try:
        await validate_identities(
            store,
            platform,
            settings.signing_key(),
            control_scope=f"production:{settings.backend_tenant}"
            if settings.profile == "production"
            else "local",
        )
        ledger = LocalExperimentLedger(f"{settings.database}.ledger")
        budget = BudgetService(store, governance=platform.governance)
        bindings = BindingsService(
            store,
            registry=platform.registry,
            genome=platform.genome,
            governance=platform.governance,
            kills=platform.kills,
        )
        runs = RunService(
            store,
            bindings=bindings,
            registry=platform.registry,
            genome=platform.genome,
            budget=budget,
            kills=platform.kills,
            ledger=ledger,
        )
        verifier = getattr(platform, "verification", None)
        if verifier is not None:
            if hasattr(verifier, "attach_store"):
                verifier.attach_store(store)

            async def _verdict_final(verdict: Any) -> None:
                """判定落定后：写实验记录 + 证据事件，并把结果留给晋升决策读。"""

                if verdict.run_id is None:
                    return
                entry_id = ledger.find_entry(
                    owner_id=verdict.bot_owner_id,
                    bot_id=verdict.bot_id,
                    run_id=verdict.run_id,
                    candidate_id=verdict.candidate,
                )
                if entry_id is not None:
                    await ledger.record_verdict(
                        entry_id,
                        VerdictRecord(
                            status=LedgerVerdictStatus(verdict.status.value),
                            reason=",".join(c.value for c in verdict.reasons_categories),
                            aggregates={
                                name: {
                                    "cases": aggregate.cases,
                                    "seeds": aggregate.seeds,
                                    "mean_delta": aggregate.mean_delta,
                                    "ci": list(aggregate.ci),
                                }
                                for name, aggregate in _strategy_aggregates(verdict).items()
                            },
                        ),
                    )
                store.events.append(
                    "verdict.finalized",
                    run_id=verdict.run_id,
                    details={
                        "candidate_id": verdict.candidate,
                        "revision": verdict.revision,
                        "verdict_status": verdict.status.value,
                    },
                )

        # 08 门禁：只读判定 + 绑定上限，产出决策；切片二不移动 `active`。
        promotion = None
        if verifier is not None and getattr(verifier, "store", None) is not None:
            from .core.evolution_promote import (
                GateDecisionStore,
                LocalPromotionService,
                RiskTier,
                VerdictPending,
            )

            decisions = GateDecisionStore(store.conn)

            async def _ceiling(bot: Any) -> Any:
                row = store.conn.execute(
                    "SELECT document FROM evolution_bindings WHERE owner_id=? AND bot_id=?",
                    (bot.owner_id, bot.bot_id),
                ).fetchone()
                if row is None:
                    return None
                try:
                    document = json.loads(row["document"])
                except (TypeError, ValueError):
                    return None
                values = {
                    item.get("auto_promote_ceiling")
                    for item in document.get("bindings") or []
                    if isinstance(item, dict)
                }
                tiers = [RiskTier(value) for value in values if value in {tier.value for tier in RiskTier}]
                return max(tiers, key=lambda tier: tier.rank) if tiers else None

            promotion = LocalPromotionService(verdicts=verifier.store, store=decisions, ceiling_for=_ceiling)

            async def _gate_after_verdict(verdict: Any) -> None:
                if verdict.run_id is None or promotion is None:
                    return
                try:
                    decision = await promotion.decide(
                        SimpleNamespace(owner_id=verdict.bot_owner_id, bot_id=verdict.bot_id),
                        verdict.candidate,
                    )
                except VerdictPending:
                    return
                store.events.append(
                    "gate.decided",
                    run_id=verdict.run_id,
                    details={
                        "candidate_id": decision.candidate_id,
                        "revision": verdict.revision,
                        "gate_outcome": decision.outcome.value,
                        "risk_tier": decision.risk_tier.value,
                    },
                )

            # 判定先落定，再由门禁消费它；两个回调按顺序执行。
            verifier.on_final = _compose_final(_verdict_final, _gate_after_verdict)

        if verifier is not None and hasattr(verifier, "resume_pending"):

            async def _bot_for(verdict: Any) -> Any:
                """按 run 行还原真实 BotRef：只按 id 重建会丢掉 entity_id。"""

                row = store.conn.execute(
                    "SELECT owner_id, entity_id, bot_id, engine_type, bot_type, entity_type "
                    "FROM evolution_run WHERE run_id=?",
                    (verdict.run_id,),
                ).fetchone()
                if row is None:
                    return None
                from avernet_genome.models import BotRef

                return BotRef(
                    row["entity_id"],
                    row["bot_id"],
                    row["owner_id"],
                    row["engine_type"],
                    row["bot_type"],
                    row["entity_type"],
                )

            # 进程内流水线不跨重启：库里停在 pending 的判定在这里补跑。
            resumed = await verifier.resume_pending(_bot_for)
            if resumed:
                import logging

                logging.getLogger(__name__).info("evolution.verdicts.resumed count=%s", resumed)

        jobs = JobService(store, lease_seconds=settings.lease_seconds)
        operations = OperationService(store, budget=budget)
        issuer = JobTokenIssuer(settings.signing_key())
        channel = JobChannel(
            store=store,
            jobs=jobs,
            genome=platform.genome,
            tokens=issuer,
            experience_factory=platform.experience,
            operations=operations,
            budget=budget,
            ledger=ledger,
            verifier=verifier,
        )

        async def context(run, **kwargs):
            kwargs["experience_provider"] = platform.experience.for_bot(run.bot)
            return await build_run_context(
                run, store=store, genome=platform.genome, budget=budget, operations=operations, **kwargs
            )

        if platform.sandbox is not None:
            host: Any = HostPool(
                tuple(
                    ContainerHost(
                        sandbox=platform.sandbox,
                        store=store,
                        jobs=jobs,
                        runs=runs,
                        image_of=lambda _, image=image: image,
                        job_endpoint=settings.job_endpoint,
                        tokens=issuer,
                        strategy_refs=tuple(refs),
                    )
                    for image, refs in image_groups.items()
                )
            )
        else:
            host = InProcessHost(
                strategies=platform.strategies,
                store=store,
                jobs=jobs,
                operations=operations,
                budget=budget,
                genome=platform.genome,
                ctx_factory=context,
                ledger=ledger,
                promotion=promotion,
                verifier=verifier,
                heartbeat_s=settings.heartbeat_s,
                experience_provider=None,
            )
        scheduler = TriggerScheduler(store=store, runs=runs, genome=platform.genome, kills=platform.kills)
        runner = EvolutionRunner(
            host=host,
            scheduler=scheduler,
            jobs=jobs,
            enabled=settings.consumer,
            poll_interval_s=settings.poll_interval_s,
        )
        return Runtime(
            store,
            ledger,
            EvolutionApi(
                bindings=bindings,
                runs=runs,
                registry=platform.registry,
                ledger=ledger,
                verification=verifier,
                promotion=promotion,
                genome=platform.genome,
            ),
            channel,
            runner,
            platform,
        )
    except BaseException:
        if "ledger" in locals():
            ledger.conn.close()
        store.conn.close()
        raise
