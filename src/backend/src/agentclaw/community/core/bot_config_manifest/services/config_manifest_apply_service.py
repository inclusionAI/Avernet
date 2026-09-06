"""Applying a bot's configuration manifest (issue #1472).

Owns the lifecycle around the orchestrator: the lock, the re-validation, the
record's two writes, and handing the work to the queue that runs it.

**Apply is started, not awaited.** :meth:`start_apply` answers as soon as it can
answer — with an ``apply_id`` — and the orchestrator runs elsewhere. Applying is
device I/O today and network fetching from W5, so a caller holding an HTTP
connection across it would be a caller timing out. It is also the shape W13's
``APPLYING`` poll state needs: a state you can observe only exists if the work is
something you start and then ask about.

**Where "elsewhere" is changed with W13, and the difference is durability.** W4
ran the orchestrator on a daemon thread; it now runs as a ``config_manifest.apply``
task (see ``apply/apply_task.py``). A thread dies with its pod and takes the apply
with it, which stopped being merely untidy once creation began to *depend* on an
apply completing — the startup-script row must exist before the start command is
composed. A task is re-claimed after its lease expires and finishes.

Nothing above the enqueue moved, and that is deliberate: the lock, the
re-validation and the ``RUNNING`` row all still happen on the caller's thread, so
every observable behaviour of ``POST …/config-manifest/apply`` is what it was.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Optional


from agentclaw.community.core.bot_config_manifest.apply.budget import (
    ApplyFetchBudget,
)
from agentclaw.community.core.bot_config_manifest.apply.carry_forward import (
    carry_forward,
)
from agentclaw.community.core.bot_config_manifest.apply.activation_delegates import (
    ProjectingActivation,
)
from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    DeliveryStrategy,
    DeliveryStrategyFactory,
    MaterialiserPorts,
    Redeliver,
)
from agentclaw.community.core.bot_config_manifest.apply.apply_task import (
    APPLY_TASK_DEADLINE_SECONDS,
    APPLY_TASK_TYPE,
    build_apply_task_payload,
    phases_from_payload,
)
from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetcher,
)
from agentclaw.community.core.bot_config_manifest.apply.identity_port import (
    ManifestIdentityPort,
)
from agentclaw.community.core.bot_config_manifest.apply.resource_port import (
    ManifestResourcePort,
)
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitSourceClient,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    APPLY_BUDGET_S,
    APPLY_FETCH_TOTAL_LIMIT,
)
from agentclaw.community.core.bot_config_manifest.apply.order import (
    ApplyPhase,
)
from agentclaw.community.core.bot_config_manifest.apply.orchestrator import (
    ApplyOrchestrator,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyConstruct,
    ApplyReport,
    ApplyStatus,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    build_materialisers,
)
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.services.apply_termination import (
    parse_started_at,
    record_engine_failure,
    terminate_on_launch_failure,
    terminate_unstartable,
)
from agentclaw.community.core.bot_config_manifest.services.apply_report_codec import (
    report_from_payload,
)
from agentclaw.community.core.bot_config_manifest.services.apply_lock_reaper import (
    APPLY_LOCK_TTL_SECONDS,
    is_abandoned,
    reap_stale_lock,
)
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_apply_service_protocol import (
    ApplyAccepted,
    BotConfigManifestApplyServiceProtocol,
    ManifestApplyInProgressError,
)
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    BotConfigManifestServiceProtocol,
)
from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.mcp.mcp_auth_service_protocol import (
    MCPAuthServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepositoryProtocol,
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.core.skill_center.direct_activation_service_protocol import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.core.skill_center.local_skill_upload_service_protocol import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.skill_package import (
    SkillPackageValidator,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)
from agentclaw.community.utils.env_utils import get_current_env

if TYPE_CHECKING:  # pragma: no cover - import-time cycle, see below
    from agentclaw.community.core.bot_config_manifest.cli_tools.service import (
        CliToolService,
    )
    from agentclaw.community.core.task_queue.services.task_queue_service import (
        TaskQueueService,
    )

logger = get_logger()



class ManifestApplyBotMissingError(RuntimeError):
    """The bot a queued apply targets no longer exists."""

    def __init__(self, bot_id: str) -> None:
        super().__init__(f"bot {bot_id} not found for a queued apply")



def _engine_and_bot_type(
    bot: Optional[dict],
    engine_type: Optional[str],
    bot_type: Optional[str],
) -> tuple[str, str]:
    """The two values every capability question is answered from.

    One helper because the record and record-free paths must not drift: a bot
    whose engine is read one way here and another way there would validate
    against one set of capabilities and apply against a different one.
    """
    if bot is not None:
        return str(bot.get("active_engine") or ""), str(bot.get("bot_type") or "")
    return str(engine_type or ""), str(bot_type or "")

#: How many report rows the strict-mode baseline read walks back through.
#: The common case resolves the newest report on the first row; the walk
#: exists so a run of failed fetches (each carrying no resolution for the
#: source it could not reach) cannot wipe a baseline that a report further
#: back still records. Bounded because a source a document no longer names
#: must age out eventually, rather than be remembered forever.
_BASELINE_HISTORY_APPLIES = 10


class BotConfigManifestApplyService(BotConfigManifestApplyServiceProtocol):
    """Start applies, and read what they did."""

    # Deliberately **not** ``@inject``: ``task_queue_provider`` is annotated with a
    # ``TYPE_CHECKING``-only name (the queue module imports the DI container at
    # module scope, so it cannot be imported here), and ``@inject`` would make the
    # injector resolve these hints at construction and fail on it. The DI module
    # builds this service with an explicit provider instead.
    def __init__(
        self,
        manifest_service: BotConfigManifestServiceProtocol,
        apply_repository: BotConfigManifestApplyRepositoryProtocol,
        lock_repository: BotConfigManifestApplyLockRepositoryProtocol,
        script_service_provider: Callable[[], BotStartupScriptServiceProtocol],
        activation_service_provider: Callable[[], DirectActivationServiceProtocol],
        mcp_auth_service_provider: Callable[[], MCPAuthServiceProtocol],
        identity_service_provider: Callable[[], ManifestIdentityPort],
        upload_service_provider: Callable[[], LocalSkillUploadServiceProtocol],
        capability_reader_provider: Callable[[], BotCapabilityStateReaderProtocol],
        package_validator_provider: Callable[[], SkillPackageValidator],
        entry_fetcher_provider: Callable[[], EntryFetcher],
        resource_service_provider: Callable[[], ManifestResourcePort],
        cli_tool_service_factory: Callable[[str], "CliToolService"],
        git_client_provider: Callable[[], GitSourceClient],
        task_queue_provider: Callable[[], "TaskQueueService"],
        bot_repository: BotRepository,
        *,
        is_teclaw: Optional[Callable[[Optional[str]], bool]] = None,
        teclaw_platform_managed: bool = False,
        teclaw_platform_ports_provider: Optional[Callable[[], MaterialiserPorts]] = None,
        redeliver: Optional[Redeliver] = None,
    ) -> None:
        self._manifests = manifest_service
        self._applies = apply_repository
        self._locks = lock_repository
        # Lazy providers rather than the services themselves, the way the
        # sibling manifest service holds its teclaw test: the bot-configuration
        # graph reaches back into this module's package, and holding concrete
        # instances would close an import cycle at construction.
        self._script_service_provider = script_service_provider
        self._activation_service_provider = activation_service_provider
        self._mcp_auth_service_provider = mcp_auth_service_provider
        # W5's two fetch-consuming materialisers take their services the same
        # way — each sits deeper in the bot-configuration graph, and holding
        # one directly would close the same cycles. The identity service is
        # named by its narrow apply-side key (``apply/identity_port.py``): the
        # real service has no Protocol (one implementation, the waiver the
        # identity router records), and the port exists to key a lazy
        # provider without importing the device graph.
        self._identity_service_provider = identity_service_provider
        self._upload_service_provider = upload_service_provider
        self._capability_reader_provider = capability_reader_provider
        self._package_validator_provider = package_validator_provider
        self._entry_fetcher_provider = entry_fetcher_provider
        # The same laziness for W6's resources materialiser: the resource
        # file service dispatches to devices, another arm of the same
        # bot-configuration graph that made every provider above lazy.
        self._resource_service_provider = resource_service_provider
        # W9: the same component the management API installs through, built
        # per engine family because the family decides which delivery port sits
        # inside it. Held as a factory rather than an instance for the reason
        # every provider above is lazy — it reaches the device graph.
        self._cli_tool_service_factory = cli_tool_service_factory
        # W7's git transport, held the same lazy way: the sessions built
        # above reach it by lookup rather than by a held instance, so no
        # client state can outlive the apply that asked for it. The type is
        # the fetch-side Protocol — already in this file's import tree via
        # ``source_session`` — and a runtime import rather than a
        # TYPE_CHECKING one because the injector resolves this constructor's
        # string annotations against this module's globals.
        self._git_client_provider = git_client_provider
        # The queue that now runs the work, and the reader that rebuilds what the
        # payload deliberately does not carry.
        #
        # The queue is **lazy, and must stay that way**:
        # ``task_queue_service`` imports ``community.di`` at module scope, which
        # pulls the whole container graph, which reaches back here — importing it
        # eagerly from this module is a circular import, not a style preference.
        # The repository has no such problem and is injected directly.
        self._task_queue_provider = task_queue_provider
        self._bots = bot_repository
        # W8: the delivery seam. The factory is the one reader of the
        # platform-managed switch; ARCA's ports are the providers above, held
        # as a thunk so they are resolved per apply like everything else here.
        # ``is_teclaw`` is the engine authority (``TeclawProvisionService``),
        # passed in by the DI module; ``None`` — a test constructing the
        # service without one — makes every bot ARCA, which is the pre-W8
        # behaviour and never a silent teclaw misroute in production, where
        # the module always binds it.
        self._strategies = DeliveryStrategyFactory(
            is_teclaw=is_teclaw or (lambda _engine: False),
            teclaw_platform_managed=teclaw_platform_managed,
            arca_ports=self._arca_ports,
            teclaw_platform_ports=teclaw_platform_ports_provider,
            redeliver=redeliver,
            # W9: the teclaw-bound CLI service, so a teclaw bot never gets the
            # ARCA delivery port for this category whatever the switch says.
            teclaw_cli_tool_service=lambda: cli_tool_service_factory("teclaw"),
        )

    # ── starting ────────────────────────────────────────────────────────────

    def start_apply(
        self,
        *,
        entity_id: str,
        bot_id: str,
        bot: Optional[dict] = None,
        owner_id: str,
        actor_id: str,
        audit_actor: Optional[str] = None,
        trigger: str = "explicit",
        phases: frozenset[ApplyPhase],
        engine_type: Optional[str] = None,
        bot_type: Optional[str] = None,
        carry_from_apply_id: Optional[str] = None,
    ) -> ApplyAccepted:
        """Lock, validate, record ``RUNNING``, enqueue the work, return the id.

        The order is the contract. Both refusals happen **before** an id exists,
        so a caller never holds a handle to an apply that never ran.

        **What runs where, and why the split is here.** Everything above the
        enqueue is synchronous on the caller's thread — the lock (so a concurrent
        apply still raises ``ManifestApplyInProgressError``), the re-validation
        (so an invalid stored document still raises *to the caller*), the
        ``apply_id`` and the ``RUNNING`` row. Only the work is handed off. That
        is what keeps ``POST …/config-manifest/apply``'s contract identical to
        what W4 shipped while the executor underneath it changed from a daemon
        thread to a durable task.

        **The lock spans the handoff**: acquired here, released by the handler
        using the token in the payload. A task that never runs — a deployment
        with the worker disabled — leaves a lock the TTL reaps, which is the same
        outcome a dead thread had.

        ``bot`` is optional. It is ``None`` on exactly one path: W13 applies the
        pre-container phase **before** the bot record is created, and supplies
        ``engine_type`` / ``bot_type`` from the creation request instead.

        ``actor_id`` and ``audit_actor`` are two different things and must not be
        collapsed. ``actor_id`` is the **principal** every downstream
        authorization check is made against — ``can_manage_bot``,
        ``check_mcp_permission_detail``. ``audit_actor`` is a *label* for the
        record's actor column, and for an application caller it is a synthetic
        string (``app:<id>:on-behalf-of:<user>``) that is deliberately not a
        principal. Passing the label as the principal denied every application
        caller: the activation service compared that string against owner and
        collaborator rows and found nobody. It defaults to ``actor_id`` so a
        caller with nothing to distinguish keeps the obvious behaviour.
        """
        env = get_current_env()
        lock = self._locks.acquire(
            env=env, entity_id=entity_id, bot_id=bot_id, holder_user_id=actor_id
        )
        if lock is None:
            if not reap_stale_lock(
                self._locks, env=env, entity_id=entity_id, bot_id=bot_id
            ):
                raise ManifestApplyInProgressError(bot_id)
            lock = self._locks.acquire(
                env=env, entity_id=entity_id, bot_id=bot_id, holder_user_id=actor_id
            )
            if lock is None:
                raise ManifestApplyInProgressError(bot_id)

        try:
            # Called for its **exception**, not its value: the parsed document
            # is rebuilt by the handler, so nothing here needs it. What this
            # buys is that a document which cannot be applied is refused
            # synchronously, to the caller, before an ``apply_id`` exists —
            # exactly as it was before the work moved to the queue.
            #
            # It was validated at ``PUT``, so why again? Because validity is
            # relative to the bot, and the bot moves:
            #
            # * **The engine changed.** A document declaring an
            #   ``identity`` file only Claude Code accepts validated against a
            #   Claude Code bot; the bot has since been switched to another
            #   engine, and the capability set it resolves against no longer
            #   admits that construct.
            # * **The build changed.** A category whose materialiser has been
            #   withdrawn since the ``PUT`` — or a document written against a
            #   newer schema and rolled back to.
            #
            # Neither is hypothetical enough to skip: both leave a stored
            # document that was valid when written and is not now, and both
            # would otherwise surface as a failed apply on a live bot rather
            # than a ``422`` on the request that asked for it.
            self._parsed_or_empty(
                entity_id=entity_id,
                bot_id=bot_id,
                bot=bot,
                engine_type=engine_type,
                bot_type=bot_type,
            )
            apply_id = uuid.uuid4().hex
            started_at = datetime.now()
            self._applies.start(
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                apply_id=apply_id,
                trigger=trigger,
                # The audit label, never the principal — this column is the one
                # place the application-formatted value belongs.
                actor=audit_actor or actor_id,
                report=json.dumps(
                    ApplyReport(
                        apply_id=apply_id,
                        bot_id=bot_id,
                        trigger=trigger,
                        status=ApplyStatus.RUNNING,
                        started_at=started_at,
                    ).as_payload()
                ),
            )
        except Exception:
            # Nothing started, so the lock must not be left behind.
            self._locks.release(
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                lock_token=lock.lock_token,
            )
            raise

        try:
            # Handed to the queue, not to a thread. The tenant is read here,
            # inside the request, and carried in the payload: the queue has no
            # tenant column and no request context survives to handler time, so
            # the thread-era ``bind_current_avernet_tenant`` wrapper has nothing
            # to wrap. The handler opens its own scope from the payload.
            self._task_queue_provider().enqueue(
                APPLY_TASK_TYPE,
                build_apply_task_payload(
                    apply_id=apply_id,
                    entity_id=entity_id,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    actor_id=actor_id,
                    env=env,
                    tenant=get_current_avernet_tenant(),
                    trigger=trigger,
                    lock_token=lock.lock_token,
                    started_at=started_at.isoformat(),
                    phases=phases,
                    engine_type=engine_type,
                    bot_type=bot_type,
                    carry_from_apply_id=carry_from_apply_id,
                ),
                APPLY_TASK_DEADLINE_SECONDS,
            )
        except BaseException as exc:
            # The RUNNING row exists but the work never started. Written for a
            # thread that could not be created; an enqueue that fails leaves the
            # identical state, so it keeps the identical answer — and that answer
            # is the better one: without the terminal write a poller sees a
            # lock-less RUNNING row, and the next apply waits out the stale-lock
            # TTL (30 minutes of ManifestApplyInProgressError) for work that never
            # ran. The apply is terminally FAILED here, the lock released for the
            # next attempt, and the caller hears the original failure.
            terminate_on_launch_failure(
                self._applies,
                self._locks,
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                apply_id=apply_id,
                trigger=trigger,
                started_at=started_at,
                lock_token=lock.lock_token,
                exc=exc,
            )
            raise

        return ApplyAccepted(apply_id=apply_id, status=ApplyStatus.RUNNING)

    def _run(
        self,
        *,
        ctx: ApplyContext,
        parsed: dict,
        apply_id: str,
        trigger: str,
        started_at: datetime,
        phases: frozenset[ApplyPhase],
        lock_token: str,
        carry_from_apply_id: Optional[str] = None,
    ) -> None:
        """The background half. Always terminates the report and the lock.

        Both the terminal write and the lock release are in ``finally`` blocks:
        a raising orchestrator must not leave a report polling forever, and must
        not leave the bot locked against every future apply.
        """
        report: ApplyReport | None = None
        try:
            strategy = self.delivery_for_engine(ctx.engine_type)
            report = asyncio.run(
                self._apply_and_finish(
                    strategy,
                    ctx,
                    parsed,
                    apply_id=apply_id,
                    trigger=trigger,
                    started_at=started_at,
                    phases=phases,
                )
            )
        except Exception as exc:  # noqa: BLE001 - a daemon thread has no caller
            logger.exception(
                "[manifest_apply] apply raised, apply_id=%s, bot_id=%s",
                apply_id,
                ctx.bot_id,
            )
            report = ApplyReport(
                apply_id=apply_id,
                bot_id=ctx.bot_id,
                trigger=trigger,
                status=ApplyStatus.FAILED,
                started_at=started_at,
                finished_at=datetime.now(),
                categories=(),
            )
            record_engine_failure(report, exc)
        else:
            report = carry_forward(
                report,
                ctx=ctx,
                carry_from_apply_id=carry_from_apply_id,
                applies=self._applies,
                to_report=self._to_report,
            )
        finally:
            try:
                if report is not None:
                    self._applies.finish(
                        env=ctx.env,
                        entity_id=ctx.entity_id,
                        bot_id=ctx.bot_id,
                        apply_id=apply_id,
                        status=report.status.value,
                        report=json.dumps(report.as_payload()),
                    )
            finally:
                # W7: the session's checkout trees are this process's to
                # clean or nobody's — close runs whether the finish write
                # succeeded or not, and wrapped so a close that raised still
                # frees the bot. The resolutions survive the close: the
                # report above has already read them out.
                try:
                    if ctx.source_session is not None:
                        ctx.source_session.close()
                finally:
                    self._locks.release(
                        env=ctx.env,
                        entity_id=ctx.entity_id,
                        bot_id=ctx.bot_id,
                        lock_token=lock_token,
                    )

    def run_apply_task(self, payload: dict) -> None:
        """Execute one apply from its task payload. Called only by the handler.

        Rebuilds what the payload deliberately does not carry — the bot record and
        the parsed document (see ``build_apply_task_payload``) — then runs the same
        ``_run`` body the daemon thread used to.

        **The tenant is re-established here, from the payload.** The queue has no
        tenant column and no request context survives to handler time, so
        ``bind_current_avernet_tenant`` cannot help. Getting this wrong fails
        *silently*: ``get_current_avernet_tenant()`` is a total function that
        returns the **default** tenant outside a request rather than raising, so a
        handler that dropped this scope would not crash — it would substitute the
        wrong ``${BOT_TENANT}`` and read and write the manifest tables under the
        wrong tenant. That is an isolation failure with nothing raised anywhere to
        announce it, which is why it is pinned by a test rather than a comment.
        """
        tenant = str(payload.get("tenant") or "")
        with avernet_tenant_scope(tenant):
            bot_id = str(payload["bot_id"])
            try:
                ctx, parsed = self._rebuild(payload)
            except Exception as exc:  # noqa: BLE001 - see below
                # The rebuild can fail for reasons a retry cannot fix: the bot's
                # engine changed since the enqueue and the stored document no
                # longer validates for it, or the row is corrupt. Letting that
                # escape would hand the worker an exception it treats as a retry,
                # and the apply would loop until its deadline with the lock still
                # held and the record still RUNNING. Terminate it here instead —
                # the same outcome ``_run`` gives a raising orchestrator.
                logger.exception(
                    "[manifest_apply] could not rebuild apply_id=%s for bot_id=%s",
                    payload.get("apply_id"),
                    bot_id,
                )
                terminate_unstartable(self._applies, self._locks, payload, exc)
                return
            self._run(
                ctx=ctx,
                parsed=parsed,
                apply_id=str(payload["apply_id"]),
                trigger=str(payload["trigger"]),
                started_at=parse_started_at(payload.get("started_at")),
                phases=phases_from_payload(payload["phases"]),
                lock_token=str(payload["lock_token"]),
                carry_from_apply_id=payload.get("carry_from_apply_id"),
            )

    def _rebuild(self, payload: dict) -> tuple[ApplyContext, dict]:
        """The context and document the payload deliberately does not carry."""
        entity_id = str(payload["entity_id"])
        bot_id = str(payload["bot_id"])
        # Re-read rather than trust a serialised copy: the record may have
        # moved since the enqueue, and for the pre-container phase there is
        # no record at all yet.
        bot = self._bot_or_none(entity_id=entity_id, bot_id=bot_id)
        if bot is None and not payload.get("engine_type"):
            # No record and nothing to stand in for one. That is not the
            # pre-container phase — it is a bot that went away between the
            # enqueue and now, and applying against defaulted capabilities
            # would be a guess.
            raise ManifestApplyBotMissingError(bot_id)
        parsed = self._parsed_or_empty(
            entity_id=entity_id,
            bot_id=bot_id,
            bot=bot,
            engine_type=payload.get("engine_type"),
            bot_type=payload.get("bot_type"),
        )
        ctx = self._context(
            bot_id=bot_id,
            bot=bot,
            owner_id=str(payload["owner_id"]),
            actor_id=str(payload["actor_id"]),
            entity_id=entity_id,
            env=str(payload["env"]),
            engine_type=payload.get("engine_type"),
            bot_type=payload.get("bot_type"),
            # The id this context's fetch pipeline stamps into every receipt, so
            # the linkage column answers "what did THIS apply fetch" as an
            # indexed read.
            apply_id=str(payload["apply_id"]),
            # The two apply-scope promises of fetch/limits.py, made real: one
            # ledger per apply, consulted before each entry's fetch and charged
            # after. It is built here rather than at the enqueue because its
            # deadline is a monotonic reading of *this* process' clock, and
            # because the apply's duration is the handler's, not the caller's.
            budget=ApplyFetchBudget(
                deadline=time.monotonic() + APPLY_BUDGET_S,
                total_bytes=APPLY_FETCH_TOTAL_LIMIT,
            ),
            # W7 built this in the request thread "so the worker never races a
            # baseline read". It is built here for the same two reasons the
            # budget is: the session owns checkout trees on disk, and those
            # belong to the process that applies them rather than to the one
            # that enqueued — across a restart the request process may not
            # exist. The race W7 named is still closed, by the lock rather than
            # by the thread: it is acquired before the enqueue and released by
            # this handler, so the baseline read happens inside the same held
            # lock the request thread's did, with no second apply between them.
            source_session=SourceSession(
                sources=parsed.get("sources") or {},
                baselines=self._last_resolutions(
                    entity_id=entity_id, bot_id=bot_id
                ),
                git=self._git_client_provider(),
            ),
        )
        return ctx, parsed

    def _bot_or_none(self, *, entity_id: str, bot_id: str) -> Optional[dict]:
        """The bot record, or ``None`` when it does not exist yet.

        ``None`` is an ordinary state, not an error: W13's pre-container phase
        runs before the record is written, which is the whole reason that phase
        can guarantee the startup-script row exists before the start command is
        composed.
        """
        return self._bots.get_by_id_and_entity(bot_id, entity_id)

    # ── dry run ─────────────────────────────────────────────────────────────

    async def dry_run(
        self,
        *,
        entity_id: str,
        bot_id: str,
        bot: dict,
        owner_id: str,
        actor_id: str,
    ) -> ApplyReport:
        """Compute the plan. Writes nothing, and mints no id."""
        env = get_current_env()
        parsed = self._parsed_or_empty(entity_id=entity_id, bot_id=bot_id, bot=bot)
        ctx = self._context(
            bot_id=bot_id,
            bot=bot,
            owner_id=owner_id,
            actor_id=actor_id,
            entity_id=entity_id,
            env=env,
            # A dry run may fetch, but bounded the same way: a preview that
            # cannot cost an unbounded run of network time is part of what
            # keeps it honest to answer synchronously.
            budget=ApplyFetchBudget(
                deadline=time.monotonic() + APPLY_BUDGET_S,
                total_bytes=APPLY_FETCH_TOTAL_LIMIT,
            ),
            # The same per-apply source session a real apply runs under,
            # baselines and all: a dry run's strict-mode answers must be the
            # answers the real apply would give, and the resolutions it
            # reports are what the caller is previewing.
            source_session=SourceSession(
                sources=parsed.get("sources") or {},
                baselines=self._last_resolutions(entity_id=entity_id, bot_id=bot_id),
                git=self._git_client_provider(),
            ),
        )
        try:
            return await self._orchestrator(
                self.delivery_for_engine(ctx.engine_type)
            ).apply(
                ctx,
                parsed,
                # No id: a dry run appears in no history, so there is nothing to
                # address it by. Naming it explicitly rather than minting one keeps
                # "writes nothing" true of the record as well as of the bot.
                apply_id="",
                trigger="dry_run",
                started_at=datetime.now(),
                dry_run=True,
            )
        finally:
            # No worker thread exists for a dry run, so its session closes
            # here — the one terminal path whose ``finally`` is this one.
            if ctx.source_session is not None:
                ctx.source_session.close()

    # ── reading ─────────────────────────────────────────────────────────────

    def get_apply(
        self, *, entity_id: str, bot_id: str, apply_id: str
    ) -> Optional[ApplyReport]:
        """One apply's report. Scoped to the bot, so a foreign id is not found."""
        record = self._applies.get(
            env=get_current_env(),
            entity_id=entity_id,
            bot_id=bot_id,
            apply_id=apply_id,
        )
        return self._to_report(record, entity_id=entity_id, bot_id=bot_id)

    def last_apply(
        self, *, entity_id: str, bot_id: str
    ) -> Optional[ApplyReport]:
        """The newest report, or ``None`` when this bot has never applied."""
        record = self._applies.latest(
            env=get_current_env(), entity_id=entity_id, bot_id=bot_id
        )
        return self._to_report(record, entity_id=entity_id, bot_id=bot_id)

    def _last_resolutions(
        self, *, entity_id: str, bot_id: str
    ) -> dict[str, str]:
        """Each source's SHA as the last apply that RESOLVED it (W7 strict).

        The reports are where "what did we resolve" already lives
        (``ApplyReport.sources``), so strict mode reads them back rather than
        keeping a second table the two could drift apart on. The read walks a
        bounded history, **not just the newest report**: the newest apply may
        have failed to fetch a source (its report carries no resolution for
        it — a failed fetch or a strict refusal adopts nothing), and reading
        only that row would wipe the baseline, silently disarming strict mode
        and the ``keep_last`` receipt after one outage. Per source, the
        newest report that carries it wins; a report with no resolutions —
        or no reports — yields no opinions.
        """
        records = self._applies.recent(
            env=get_current_env(),
            entity_id=entity_id,
            bot_id=bot_id,
            limit=_BASELINE_HISTORY_APPLIES,
        )
        baselines: dict[str, str] = {}
        for record in records:
            report = self._to_report(record, entity_id=entity_id, bot_id=bot_id)
            if report is None:
                continue
            for source in report.sources:
                if source.resolved_sha is None:
                    continue
                # Newest wins: an earlier walk-back entry is not overwritten.
                baselines.setdefault(source.name, source.resolved_sha)
        return baselines

    # ── internals ───────────────────────────────────────────────────────────

    def materialised_constructs(self) -> frozenset[ApplyConstruct]:
        """The constructs something can actually apply in this build.

        The keys of the very registry ``_orchestrator`` builds, so a caller
        gating on this and the engine executing the apply can never disagree —
        and W5/W6 widen it by registering a materialiser rather than by anyone
        remembering to update a list. A hand-written set would drift, and the
        drift is only observable as a failed apply on a bot that already exists.
        """
        return frozenset(self._build_materialisers().keys())

    def _arca_ports(self) -> MaterialiserPorts:
        """The device-backed write targets: ARCA's, and teclaw's with the switch off."""
        return MaterialiserPorts(
            script_service=self._script_service_provider(),
            activation_service=ProjectingActivation(self._activation_service_provider()),
            mcp_auth_service=self._mcp_auth_service_provider(),
            identity_service=self._identity_service_provider(),
            upload_service=self._upload_service_provider(),
            capability_reader=self._capability_reader_provider(),
            package_validator=self._package_validator_provider(),
            entry_fetcher=self._entry_fetcher_provider(),
            resource_service=self._resource_service_provider(),
            cli_tool_service=self._cli_tool_service_factory("arca"),
        )

    def _build_materialisers(self, strategy: Optional[DeliveryStrategy] = None):
        """The one construction site for the registry.

        Named rather than inlined into ``_orchestrator`` so
        ``materialised_constructs`` reads the *same* registry the engine runs,
        instead of a second list that would drift from it. The ports come from
        the strategy (W8): the registry's *keys* are the same for every family
        — which is why ``materialised_constructs`` may build it without one —
        while what sits behind each port is the family's.
        """
        ports = strategy.ports() if strategy is not None else self._arca_ports()
        return build_materialisers(**ports.as_kwargs())

    def _orchestrator(self, strategy: DeliveryStrategy) -> ApplyOrchestrator:
        """A fresh orchestrator over the strategy's registry and phase table."""
        return ApplyOrchestrator(
            self._build_materialisers(strategy), steps=strategy.steps_for
        )

    # ── the delivery seam (W8) ──────────────────────────────────────────────

    def delivery_for_engine(self, engine_type: Optional[str]) -> DeliveryStrategy:
        """The strategy a bot of this engine applies through."""
        return self._strategies.for_engine(engine_type)

    def delivery_for_bot(self, bot: dict) -> DeliveryStrategy:
        return self._strategies.for_bot(bot)

    async def _apply_and_finish(
        self,
        strategy: DeliveryStrategy,
        ctx: ApplyContext,
        parsed: dict,
        *,
        apply_id: str,
        trigger: str,
        started_at: datetime,
        phases: frozenset[ApplyPhase],
    ) -> ApplyReport:
        """Walk the categories, then let the strategy close the apply.

        The closing step runs only after every category has been written and
        only for a real apply; its failure is a note on the report, never a
        raise (§2.7: a manifest problem does not fail what it rode on).
        """
        report = await self._orchestrator(strategy).apply(
            ctx,
            parsed,
            apply_id=apply_id,
            trigger=trigger,
            started_at=started_at,
            phases=phases,
        )
        try:
            note = await strategy.finish(ctx, report)
        except Exception as exc:  # noqa: BLE001 — recorded, never raised
            logger.exception(
                "[manifest_apply] closing step raised, apply_id=%s, bot_id=%s",
                apply_id,
                ctx.bot_id,
            )
            note = f"delivery could not be closed: {exc.__class__.__name__}"
        if note:
            report = dataclasses.replace(report, notes=report.notes + (note,))
        return report

    def _context(
        self,
        *,
        bot_id: str,
        bot: Optional[dict],
        owner_id: str,
        actor_id: str,
        entity_id: str,
        env: str,
        apply_id: Optional[str] = None,
        budget: Optional[ApplyFetchBudget] = None,
        source_session: Optional[SourceSession] = None,
        engine_type: Optional[str] = None,
        bot_type: Optional[str] = None,
    ) -> ApplyContext:
        """The identity one apply runs under, with or without a bot record.

        ``bot`` is ``None`` on exactly one path: W13 runs the pre-container phase
        **before** the bot is created, so there is no record to read. That case
        supplies ``engine_type`` / ``bot_type`` from the creation request and
        resolves capabilities from those — which is the second entry point W1
        built for this caller, not a parallel implementation. ``ApplyContext.bot``
        is then a minimal stand-in; the two shipped materialisers do not read it,
        and the one that runs in this phase (``script``) reads only the engine,
        env and tenant.
        """
        engine, kind = _engine_and_bot_type(bot, engine_type, bot_type)
        # The stand-in the docstring describes, named rather than inlined so the
        # "no record yet" case reads as one thing.
        record = bot
        if record is None:
            record = {
                "bot_id": bot_id,
                "entity_id": entity_id,
                "active_engine": engine,
                "bot_type": kind,
            }
        return ApplyContext(
            bot_id=bot_id,
            owner_id=owner_id,
            actor_id=actor_id,
            entity_id=entity_id,
            env=env,
            tenant=get_current_avernet_tenant(),
            engine_type=engine,
            bot_type=kind,
            bot=record,
            capabilities=(
                self._manifests.capabilities_for_bot(bot)
                if bot is not None
                else self._manifests.resolve_capabilities(
                    active_engine=engine, bot_type=kind
                )
            ),
            apply_id=apply_id,
            budget=budget,
            source_session=source_session,
        )

    def _parsed_or_empty(
        self,
        *,
        entity_id: str,
        bot_id: str,
        bot: Optional[dict],
        engine_type: Optional[str] = None,
        bot_type: Optional[str] = None,
    ) -> dict:
        """The stored document, re-validated, or ``{}`` when there is none.

        A bot with no manifest applies nothing and reports nothing applied —
        not an error, the same rule that makes an absent manifest read as an
        empty document rather than a 404.

        Re-validating is not paranoia: capabilities resolve from the bot's
        engine, which can change after a document is accepted, so a construct
        that was appliable at ``PUT`` may not be now.
        """
        record = self._manifests.get(entity_id=entity_id, bot_id=bot_id)
        if record is None:
            return {}
        engine, kind = _engine_and_bot_type(bot, engine_type, bot_type)
        result = self._manifests.validate(
            document=record.document,
            active_engine=engine,
            bot_type=kind,
        )
        return result.parsed

    def _to_report(
        self, record: Any, *, entity_id: str, bot_id: str
    ) -> Optional[ApplyReport]:
        """Rebuild a stored report, deriving ``FAILED`` for a stranded one.

        A report still ``RUNNING`` whose lock has gone stale belonged to a
        process that died before its ``finally`` could run. Reading it as
        ``FAILED`` is what stops a poller waiting forever, and doing it here —
        at read time — means there is no sweeper process to keep alive.
        """
        if record is None:
            return None
        payload = json.loads(record.report) if record.report else {}
        status = ApplyStatus(record.status)
        if status is ApplyStatus.RUNNING and is_abandoned(
            self._locks, env=get_current_env(), entity_id=entity_id, bot_id=bot_id
        ):
            status = ApplyStatus.FAILED
        return report_from_payload(payload, record=record, status=status)


__all__ = ["APPLY_LOCK_TTL_SECONDS", "BotConfigManifestApplyService"]
