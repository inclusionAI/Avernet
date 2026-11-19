"""Independent composition root for 01–06; evaluation/promotion are absent."""

from dataclasses import dataclass
from typing import Any

from .config import Settings
from .core.evolution_ledger import LocalExperimentLedger
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
from .identity import validate_identities
from .plugin_api import Platform


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
            EvolutionApi(bindings=bindings, runs=runs, registry=platform.registry, ledger=ledger),
            channel,
            runner,
            platform,
        )
    except BaseException:
        if "ledger" in locals():
            ledger.conn.close()
        store.conn.close()
        raise
