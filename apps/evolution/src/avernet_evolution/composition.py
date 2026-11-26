"""Independent composition root for 01–06, plus the 07 verifier when the platform provides one.

Evaluation (07) and promotion (08) are optional ports: a deployment that omits them keeps
the 01–06 behaviour, where every candidate stays `pending`.
"""

import logging
from dataclasses import dataclass
from typing import Any

from avernet_genome.models import BotRef

from .config import Settings
from .core.evolution_ledger import LocalExperimentLedger
from .core.evolution_ledger.models import VerdictRecord
from .core.evolution_ledger.models import VerdictStatus as LedgerVerdictStatus
from .core.evolution_run.api import EvolutionApi
from .core.evolution_run.bindings_service import BindingsService
from .core.evolution_run.budget_service import BudgetService
from .core.evolution_run.container_host import ContainerHost
from .core.evolution_run.context import build_run_context
from .core.evolution_run.data_scope import data_scope, run_data_scope
from .core.evolution_run.host import InProcessHost
from .core.evolution_run.host_pool import HostPool
from .core.evolution_run.job_protocol import JobChannel, JobTokenIssuer
from .core.evolution_run.job_service import JobService
from .core.evolution_run.operation_service import OperationService
from .core.evolution_run.records import Run
from .core.evolution_run.run_service import RunService
from .core.evolution_run.runner import EvolutionRunner
from .core.evolution_run.scheduler import TriggerScheduler
from .core.evolution_run.store import EvolutionStore
from .core.evolution_verify import Split, Verdict
from .identity import validate_identities
from .plugin_api import Platform


def _verdict_run(store: EvolutionStore, verdict: Verdict) -> Run | None:
    run = store.get_run(verdict.run_id)
    if run is None or (run.bot.owner_id, run.bot.bot_id, run.parent_revision, run.verification_profile) != (
        verdict.bot_owner_id,
        verdict.bot_id,
        verdict.parent_revision,
        verdict.profile,
    ):
        return None
    candidate = store.get_candidate(run.id, verdict.candidate)
    return (
        run
        if candidate is not None and candidate.revision and candidate.revision == verdict.revision
        else None
    )


def _verdict_record(verdict: Verdict) -> VerdictRecord:
    validation = verdict.splits.get(Split.VALIDATION)
    aggregates = {}
    if validation is not None and validation.ci is not None and validation.mean_delta is not None:
        aggregates["validation"] = {
            "cases": validation.cases,
            "seeds": validation.seeds,
            "mean_delta": validation.mean_delta,
            "ci": list(validation.ci),
        }
    return VerdictRecord(
        status=LedgerVerdictStatus(verdict.status.value),
        reason=",".join(category.value for category in verdict.reasons_categories),
        aggregates=aggregates,
    )


@dataclass
class Runtime:
    store: EvolutionStore
    ledger: LocalExperimentLedger
    api: EvolutionApi
    channel: JobChannel
    runner: EvolutionRunner
    platform: Platform
    verifier: Any = None

    async def close(self):
        try:
            await self.runner.shutdown()
        finally:
            try:
                if hasattr(self.verifier, "shutdown"):
                    await self.verifier.shutdown()
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
        if hasattr(verifier, "attach_store"):
            verifier.attach_store(store)

        # 08 consumes only the terminal verdict and the submitted Run's frozen policy. No apply.
        promotion = None
        if getattr(verifier, "store", None) is not None:
            from .core.evolution_promote import GateDecisionStore, LocalPromotionService

            promotion = LocalPromotionService(verdicts=verifier.store, store=GateDecisionStore(store.conn))

        async def _verdict_final(verdict: Verdict) -> None:
            run = _verdict_run(store, verdict)
            if run is None:
                raise ValueError("verdict does not match a recorded Run candidate")
            entry_id = ledger.find_entry(
                owner_id=run.bot.owner_id,
                bot_id=run.bot.bot_id,
                run_id=run.id,
                candidate_id=verdict.candidate,
            )
            if entry_id is None:
                raise ValueError("verdict has no matching Ledger submission")
            await ledger.record_verdict(entry_id, _verdict_record(verdict))
            store.record_candidate_verdict(run.id, verdict.candidate, verdict.status.value)
            details = {"candidate_id": verdict.candidate, "revision": verdict.revision}
            store.events.append(
                "verdict.finalized",
                run_id=run.id,
                details={**details, "verdict_status": verdict.status.value},
                once=True,
            )
            if promotion is not None:
                from .core.evolution_promote import RiskTier

                decision = await promotion.decide(
                    run.bot,
                    verdict.candidate,
                    run_id=run.id,
                    profile=run.verification_profile,
                    ceiling=RiskTier(run.auto_promote_ceiling)
                    if run.auto_promote_ceiling is not None
                    else None,
                )
                store.events.append(
                    "gate.decided",
                    run_id=run.id,
                    once=True,
                    details={
                        **details,
                        "gate_outcome": decision.outcome.value,
                        "risk_tier": decision.risk_tier.value,
                    },
                )

        def _bot_for(verdict: Verdict) -> BotRef | None:
            run = _verdict_run(store, verdict)
            return run.bot if run is not None else None

        def _evaluation_scope(candidate):
            run = store.get_run(candidate.run_id)
            row = store.get_candidate(candidate.run_id, candidate.candidate_id)
            if (
                run is None
                or row is None
                or row.revision != candidate.revision
                or (run.bot != candidate.bot or run.parent_revision != candidate.parent_revision)
            ):
                raise ValueError("verification does not match a recorded Run candidate")
            return data_scope(run_data_scope(run))

        if hasattr(verifier, "evaluation_scope"):
            verifier.evaluation_scope = _evaluation_scope
        if hasattr(verifier, "on_final"):
            verifier.on_final = _verdict_final
        reconcile = getattr(verifier, "reconcile_finalized", None)

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
            models=platform.models,
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
                models_provider=platform.models,
            )
        manual = None
        if platform.manual_delivery is not None:
            from .core.evolution_promote.manual import ManualPromotionService
            from .core.evolution_promote.manual_store import ManualAuthorizationStore

            manual = ManualPromotionService(
                store=ManualAuthorizationStore(store.conn),
                runs=runs,
                genome=platform.genome,
                delivery=platform.manual_delivery,
                checks=promotion,
                kills=platform.kills,
            )
        scheduler = TriggerScheduler(store=store, runs=runs, genome=platform.genome, kills=platform.kills)
        runner = EvolutionRunner(
            host=host,
            scheduler=scheduler,
            jobs=jobs,
            enabled=settings.consumer,
            poll_interval_s=settings.poll_interval_s,
            reconcile_finalized=reconcile,
        )
        if reconcile is not None:
            await reconcile()
        if hasattr(verifier, "resume_pending"):
            resumed = await verifier.resume_pending(_bot_for)
            if resumed:
                logging.getLogger(__name__).info("evolution.verdicts.resumed count=%s", resumed)
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
                manual=manual,
            ),
            channel,
            runner,
            platform,
            verifier,
        )
    except BaseException:
        try:
            if "verifier" in locals() and hasattr(verifier, "shutdown"):
                await verifier.shutdown()
        finally:
            if "ledger" in locals():
                ledger.conn.close()
            store.conn.close()
        raise
