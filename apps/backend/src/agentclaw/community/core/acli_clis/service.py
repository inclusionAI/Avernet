"""List, search, add, remove and cancel a bot runtime's acli market CLIs.

Spec: ``apps/backend/specs/2026-10-21-acli-cli-operations/spec.md``.

* **Every add, remove and cancel is a task-queue task** (``tasks.py``). The
  API enqueues it and, unless ``async`` was asked for, waits up to 30 s for it
  to end; a task still running then is answered as an operation to poll. How
  long the request waits never changes what the task does (D10).
* **One add or remove per runtime at a time.** A runtime is a bot's stage —
  ``draft`` for any bot, ``verify`` / ``online`` for a service bot — and its
  operations share one idempotency key. A second request while one is live
  is refused with the live operation (``AcliOperationInProgressError``).
* **The engine is the source of truth for what is installed** (D8): listing
  asks the stage's container. The Passport grant is written before an install
  and never revoked (D9), so anything installed in any stage is granted.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.acli_clis.errors import (
    AcliBotNotRunningError,
    AcliCliNameMismatchError,
    AcliDefaultCliError,
    AcliEngineOutdatedError,
    AcliEngineUnsupportedError,
    AcliGrantSyncError,
    AcliMarketNotConfiguredError,
    AcliMarketUnavailableError,
    AcliOperationCancelledError,
    AcliOperationFailedError,
    AcliOperationInProgressError,
    AcliOperationNotActiveError,
    AcliOperationNotFoundError,
    AcliRequestRejectedError,
    AcliStageNotAllowedError,
)
from agentclaw.community.core.acli_clis.market import AcliMarketSearch
from agentclaw.community.core.acli_clis.operations import (
    CANCEL_TASK_DEADLINE_SECONDS,
    CANCEL_TASK_TYPE,
    OPERATION_TASK_DEADLINE_SECONDS,
    OPERATION_TASK_TYPE,
    AcliAction,
    AcliOperation,
    AcliRuntime,
    OperationKind,
    OperationPayload,
    OperationStatus,
    OperationStep,
    cancel_key,
    operation_key,
    parse_task_error,
)
from agentclaw.community.core.acli_clis.port import (
    AcliMarketPage,
    AcliPort,
    AcliPortError,
    AcliTool,
)
from agentclaw.community.core.acli_clis.protocols import (
    AcliCliServiceProtocol,
    AcliOutcome,
    BotCli,
)
from agentclaw.community.core.acli_clis.repository.models import AcliOperationRecord
from agentclaw.community.core.acli_clis.repository.store import AcliOperationStore
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
    resolve_operable_permission_level,
)
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityPermissionError,
    CallerLockEpochError,
)
from agentclaw.community.core.caller_identity.guard import load_bot, lock_epoch_matches
from agentclaw.community.core.errors import DomainError
from agentclaw.community.core.mcp.services.cli_capabilities import (
    CliCapabilityManifestResolver,
)
from agentclaw.community.core.mcp.services.cli_passport_scope import (
    CliPassportScopeReconciler,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLockRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.repository.protocols.identity import (
    CallerIdentityRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.platform import (
    TaskQueueRepositoryProtocol,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.core.task_queue.types import TaskRecord, TaskStatus
from agentclaw.community.log import get_logger

logger = get_logger()

#: Engines whose containers ship acli and its endpoints (``/api/cli/acli/*``;
#: teclaw serves them under ``/api/v1/cli/acli/*``).
ACLI_ENGINES = frozenset({"openclaw", "claude_code", "teclaw"})
SERVICE_BOT_TYPE = "service"
#: How long a sync call waits for its operation (D2), and how often it looks.
SYNC_WAIT_SECONDS = 30.0
WAIT_POLL_SECONDS = 1.0


class AcliCliService(AcliCliServiceProtocol):
    """Enqueues acli operations and reports on them; the tasks do the work."""

    def __init__(
        self,
        *,
        bot_repository: BotRepository,
        lock_repository: BotCollabLockRepositoryProtocol,
        identity_repository: CallerIdentityRepositoryProtocol,
        scope_reconciler: CliPassportScopeReconciler,
        manifest_resolver: CliCapabilityManifestResolver,
        port: AcliPort,
        market: AcliMarketSearch,
        collaborators: CollaboratorServiceProtocol,
        task_queue: TaskQueueService,
        task_repository: TaskQueueRepositoryProtocol,
        store: AcliOperationStore,
    ) -> None:
        self._bots = bot_repository
        self._locks = lock_repository
        self._identities = identity_repository
        self._reconciler = scope_reconciler
        self._manifest = manifest_resolver
        self._port = port
        self._market = market
        self._collaborators = collaborators
        self._tasks = task_queue
        self._task_repo = task_repository
        self._store = store

    # ── reads ────────────────────────────────────────────────────────────

    async def list_bot_clis(
        self, *, bot_id: str, entity_id: str, actor_id: str, stage: str | None = None
    ) -> list[BotCli]:
        """The CLIs installed in the stage's container, plus the bot's defaults.

        ``actor_id`` must be at least a member of the bot. The default CLIs are
        installed by the image at every start rather than through acli, so
        they are listed from the bot's profile.
        """
        bot = await asyncio.to_thread(self._readable_bot, bot_id, entity_id, actor_id)
        runtime = self._runtime(bot, stage)
        try:
            tools = await self._port.list_tools(runtime)
        except AcliPortError as error:
            raise _container_error(error.code, error.message) from error
        defaults = self._default_codes(bot)
        overrides = self._identities.list_draft_cli_call_types(
            int(bot["id"]), str(bot["active_engine"])
        )
        granted = {
            str(item["cli_code"]): item
            for item in self._reconciler.current_passport_cli_items(bot=bot)
        }

        def item(code: str, description: str) -> BotCli:
            held = granted.get(code, {})
            return BotCli(
                cli_code=code,
                cli_name=str(held.get("cli_name") or code),
                cli_desc=str(held.get("cli_desc") or description),
                identity_mode=_mode(overrides.get(code))
                or str(held.get("identity_mode") or "owner"),
                default=code in defaults,
            )

        listed = [item(tool.name, tool.description) for tool in tools]
        seen = {cli.cli_code for cli in listed}
        listed += [item(code, "") for code in sorted(defaults) if code not in seen]
        return listed

    async def search_market(
        self, *, keyword: str, page_no: int, page_size: int
    ) -> AcliMarketPage:
        """One page of the acli market. The market is the same for every bot,
        so no bot or container is involved."""
        return await asyncio.to_thread(
            self._market.search, keyword=keyword, page_no=page_no, page_size=page_size
        )

    async def get_operation(
        self, *, bot_id: str, entity_id: str, actor_id: str, operation_id: str
    ) -> AcliOperation:
        """One add, remove or cancel of this bot, running or ended."""
        bot = await asyncio.to_thread(self._readable_bot, bot_id, entity_id, actor_id)
        record = await asyncio.to_thread(self._store.get, operation_id)
        if record is None or record.bot_pk != int(bot["id"]):
            raise AcliOperationNotFoundError(operation_id)
        return await asyncio.to_thread(self._view, record)

    async def live_operation(
        self, *, bot_id: str, entity_id: str, actor_id: str, stage: str | None = None
    ) -> AcliOperation | None:
        """The stage's running add or remove, if any (there is at most one).

        A cancel in progress shows as its ``cancelling`` step; the cancel
        itself reads back by the id its request answered with.
        """
        bot = await asyncio.to_thread(self._readable_bot, bot_id, entity_id, actor_id)
        runtime = self._runtime(bot, stage)
        record = await asyncio.to_thread(
            self._store.latest, bot_pk=int(bot["id"]), stage=runtime.stage,
            kind=OperationKind.OPERATION,
        )
        if record is None:
            return None
        view = await asyncio.to_thread(self._view, record)
        return view if view.running else None

    # ── writes ───────────────────────────────────────────────────────────

    async def add(
        self,
        *,
        bot_id: str,
        entity_id: str,
        actor_id: str,
        cli_code: str,
        lock_epoch: int | None,
        stage: str | None = None,
        run_async: bool = False,
    ) -> AcliOutcome:
        """Grant, then install, ``cli_code`` in the stage's container."""
        bot = await asyncio.to_thread(
            self._writable_bot, bot_id, entity_id, actor_id, lock_epoch
        )
        runtime = self._runtime(bot, stage)
        listed = await self._market_entry(cli_code)
        identity_mode = await asyncio.to_thread(self._kept_identity, bot, cli_code)
        payload = self._payload(
            bot, runtime, entity_id=entity_id, actor_id=actor_id, kind=OperationKind.OPERATION,
            action=AcliAction.INSTALL, cli_code=cli_code, identity_mode=identity_mode,
            cli_desc=listed.description if listed is not None else None,
        )
        return await self._run(payload, run_async=run_async)

    async def remove(
        self,
        *,
        bot_id: str,
        entity_id: str,
        actor_id: str,
        cli_code: str,
        lock_epoch: int | None,
        stage: str | None = None,
        run_async: bool = False,
    ) -> AcliOutcome:
        """Uninstall ``cli_code`` from the stage's container. The grant stays (D9)."""
        bot = await asyncio.to_thread(
            self._writable_bot, bot_id, entity_id, actor_id, lock_epoch
        )
        runtime = self._runtime(bot, stage)
        if cli_code in self._default_codes(bot):
            raise AcliDefaultCliError(cli_code)
        payload = self._payload(
            bot, runtime, entity_id=entity_id, actor_id=actor_id, kind=OperationKind.OPERATION,
            action=AcliAction.UNINSTALL, cli_code=cli_code,
        )
        return await self._run(payload, run_async=run_async)

    async def cancel(
        self,
        *,
        bot_id: str,
        entity_id: str,
        actor_id: str,
        cli_code: str,
        action: AcliAction,
        lock_epoch: int | None,
        stage: str | None = None,
        run_async: bool = False,
    ) -> AcliOutcome:
        """Cancel the running ``action`` of ``cli_code``; the engine reverts it."""
        action = AcliAction(action)
        bot = await asyncio.to_thread(
            self._writable_bot, bot_id, entity_id, actor_id, lock_epoch
        )
        runtime = self._runtime(bot, stage)
        target = await asyncio.to_thread(
            self._store.latest, bot_pk=int(bot["id"]), stage=runtime.stage,
            kind=OperationKind.OPERATION,
        )
        if target is None or target.action != action or target.cli_code != cli_code or not (
            await asyncio.to_thread(self._view, target)
        ).running:
            raise AcliOperationNotActiveError(f"no {action} of {cli_code!r} is running")
        payload = self._payload(
            bot, runtime, entity_id=entity_id, actor_id=actor_id, kind=OperationKind.CANCEL,
            action=action, cli_code=cli_code, target_operation_id=target.operation_id,
        )
        return await self._run(payload, run_async=run_async)

    # ── running an operation ─────────────────────────────────────────────

    async def _run(self, payload: OperationPayload, *, run_async: bool) -> AcliOutcome:
        """Enqueue ``payload`` and answer for it: at once, or once it ends (≤ 30 s)."""
        cancel = payload.kind == OperationKind.CANCEL
        result = await asyncio.to_thread(
            self._tasks.enqueue,
            CANCEL_TASK_TYPE if cancel else OPERATION_TASK_TYPE,
            payload.to_dict(),
            CANCEL_TASK_DEADLINE_SECONDS if cancel else OPERATION_TASK_DEADLINE_SECONDS,
            idempotency_key=(cancel_key if cancel else operation_key)(
                payload.bot_pk, payload.stage
            ),
        )
        if not result.created:
            live = await asyncio.to_thread(self._view_of_task, result.record)
            if cancel and live is not None and (
                live.action == payload.action and live.cli_code == payload.cli_code
            ):
                # The same cancel is already on its way: answer for that one.
                return await self._answer(live, payload.runtime, run_async=run_async)
            raise AcliOperationInProgressError(live)
        await asyncio.to_thread(self._store.create, AcliOperationRecord(
            operation_id=payload.operation_id,
            task_id=result.record.id,
            kind=payload.kind,
            action=payload.action,
            cli_code=payload.cli_code,
            bot_pk=payload.bot_pk,
            stage=payload.stage,
            target_operation_id=payload.target_operation_id,
        ))
        logger.info(
            "acli_operation_enqueued operation_id=%s kind=%s action=%s cli_code=%s "
            "bot_id=%s stage=%s actor_id=%s",
            payload.operation_id, payload.kind, payload.action, payload.cli_code,
            payload.bot_id, payload.stage, payload.actor_id,
        )
        view = await asyncio.to_thread(self._view_of_id, payload.operation_id)
        return await self._answer(view, payload.runtime, run_async=run_async)

    async def _answer(
        self, view: AcliOperation, runtime: AcliRuntime, *, run_async: bool
    ) -> AcliOutcome:
        if run_async:
            return AcliOutcome(operation=view)
        deadline = time.monotonic() + SYNC_WAIT_SECONDS
        while view.running and time.monotonic() < deadline:
            await asyncio.sleep(WAIT_POLL_SECONDS)
            view = await asyncio.to_thread(self._view_of_id, view.id)
        if view.running:
            return AcliOutcome(operation=view)
        if view.status == OperationStatus.SUCCEEDED:
            tool = None
            if view.kind == OperationKind.OPERATION and view.action == AcliAction.INSTALL:
                tool = await self._installed_tool(view, runtime)
            return AcliOutcome(operation=view, tool=tool)
        raise _container_error(
            view.error_code or OperationStatus.FAILED.value, view.error_message or ""
        )

    async def _installed_tool(self, view: AcliOperation, runtime: AcliRuntime) -> AcliTool:
        """What the engine reported installing; the code alone if it no longer says."""
        record = await asyncio.to_thread(self._store.get, view.id)
        result: Mapping[str, Any] = {}
        if record is not None and record.engine_operation_id:
            try:
                op = await self._port.get_operation(runtime, record.engine_operation_id)
            except AcliPortError:
                op = None
            result = (op.result if op is not None else None) or {}
        return AcliTool(
            name=str(result.get("name") or view.cli_code),
            command=str(result.get("command") or ""),
            description=str(result.get("description") or ""),
            version=str(result.get("version") or ""),
        )

    def _view_of_id(self, operation_id: str) -> AcliOperation:
        record = self._store.get(operation_id)
        if record is None:
            raise AcliOperationNotFoundError(operation_id)
        return self._view(record)

    def _view_of_task(self, task: TaskRecord) -> AcliOperation | None:
        operation_id = (task.payload or {}).get("operation_id")
        record = self._store.get(str(operation_id)) if operation_id else None
        return self._view(record, task) if record is not None else None

    def _view(self, record: AcliOperationRecord, task: TaskRecord | None = None) -> AcliOperation:
        """The operation as the API reports it: its task's status, read now."""
        task = task or self._task_repo.get_by_id(record.task_id)
        status = task.status if task is not None else TaskStatus.FAILED
        error_code = error_message = None
        if status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            state = OperationStatus.RUNNING
        elif status == TaskStatus.SUCCEEDED:
            state = OperationStatus.SUCCEEDED
        else:
            error_code, error_message = parse_task_error(
                task.last_error if task is not None else "lost: the operation's task is gone"
            )
            if status == TaskStatus.TIMED_OUT and error_code == OperationStatus.FAILED:
                error_code, error_message = "timeout", "the operation did not finish in time"
            state = (
                OperationStatus.CANCELLED if error_code == OperationStatus.CANCELLED
                else OperationStatus.FAILED
            )
        return AcliOperation(
            id=record.operation_id, kind=record.kind, action=record.action,
            cli_code=record.cli_code, stage=record.stage, status=state,
            step=self._step(record) if state == OperationStatus.RUNNING else None,
            error_code=error_code, error_message=error_message,
        )

    def _step(self, record: AcliOperationRecord) -> OperationStep:
        """What a running operation is doing now."""
        if record.kind == OperationKind.CANCEL or self._being_cancelled(record):
            return OperationStep.CANCELLING
        if record.engine_operation_id is None and record.action == AcliAction.INSTALL:
            return OperationStep.GRANTING
        if record.action == AcliAction.INSTALL:
            return OperationStep.INSTALLING
        return OperationStep.UNINSTALLING

    def _being_cancelled(self, record: AcliOperationRecord) -> bool:
        """Whether a running cancel targets this add or remove."""
        cancel = self._store.latest(
            bot_pk=record.bot_pk, stage=record.stage, kind=OperationKind.CANCEL
        )
        if cancel is None or cancel.target_operation_id != record.operation_id:
            return False
        task = self._task_repo.get_by_id(cancel.task_id)
        return task is not None and task.status in (TaskStatus.PENDING, TaskStatus.RUNNING)

    def _payload(
        self, bot: Mapping[str, Any], runtime: AcliRuntime, *, entity_id: str, actor_id: str,
        kind: OperationKind, action: AcliAction, cli_code: str,
        identity_mode: str | None = None,
        cli_desc: str | None = None, target_operation_id: str | None = None,
    ) -> OperationPayload:
        return OperationPayload(
            operation_id=uuid.uuid4().hex, kind=kind, action=action, cli_code=cli_code,
            bot_pk=int(bot["id"]), bot_id=runtime.bot_id, entity_id=entity_id,
            owner_id=runtime.owner_id, stage=runtime.stage, actor_id=actor_id,
            enqueued_at=time.time(), identity_mode=identity_mode, cli_desc=cli_desc,
            target_operation_id=target_operation_id, engine=runtime.engine,
        )

    # ── shared checks ────────────────────────────────────────────────────

    def _runtime(self, bot: Mapping[str, Any], stage: str | None) -> AcliRuntime:
        """The runtime ``stage`` names (D7): any stage for a service bot, default
        ``draft``; none for a bot with one runtime."""
        if stage is not None and str(bot.get("bot_type") or "") != SERVICE_BOT_TYPE:
            raise AcliStageNotAllowedError
        return AcliRuntime(
            bot_id=str(bot["bot_id"]), owner_id=str(bot["owner_id"]),
            stage=PublishStage(stage) if stage is not None else PublishStage.DRAFT,
            engine=str(bot.get("active_engine") or ""),
        )

    def _readable_bot(self, bot_id: str, entity_id: str, actor_id: str) -> Mapping[str, Any]:
        """The bot, if ``actor_id`` is at least a member of it."""
        bot = load_bot(self._bots, bot_id=bot_id, entity_id=entity_id)
        # COSEC: locating a bot does not authorize reading it.
        level = resolve_operable_permission_level(
            self._collaborators,
            bot=bot,
            user_id=actor_id,
            owner_id=str(bot.get("owner_id") or ""),
        )
        if level < PermissionLevel.MEMBER:
            raise CallerIdentityPermissionError
        self._require_acli_engine(bot)
        return bot

    def _writable_bot(
        self, bot_id: str, entity_id: str, actor_id: str, lock_epoch: int | None
    ) -> Mapping[str, Any]:
        """The bot, if ``actor_id`` may change its CLIs right now.

        It must exist for ``entity_id``, be active, be owned by ``actor_id``,
        run an engine that has acli, and ``lock_epoch`` must name the edit lock
        ``actor_id`` holds (or be ``None`` while no lock exists). Any bot type
        qualifies; adding a tool involves no caller identity.
        """
        bot = load_bot(self._bots, bot_id=bot_id, entity_id=entity_id)
        # The bot is checked before the actor: whether it can take a change
        # right now is the same answer for everyone.
        if bot.get("status") != "ACTIVE":
            raise AcliBotNotRunningError("not_running", "the bot is not running")
        # COSEC: entity-scoped lookup identifies the Bot; only its owner can change it.
        if str(bot.get("owner_id") or "") != actor_id:
            raise CallerIdentityPermissionError
        self._require_acli_engine(bot)
        if not lock_epoch_matches(
            self._locks,
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            actor_id=actor_id,
            lock_epoch=lock_epoch,
        ):
            raise CallerLockEpochError
        return bot

    @staticmethod
    def _require_acli_engine(bot: Mapping[str, Any]) -> None:
        if str(bot.get("active_engine") or "") not in ACLI_ENGINES:
            raise AcliEngineUnsupportedError

    async def _market_entry(self, cli_code: str) -> AcliTool | None:
        """The market's entry for ``cli_code``; refuses a command alias.

        ``None`` when the market cannot be asked or does not list it: the
        engine's install is then the check of last resort.
        """
        try:
            page = await asyncio.to_thread(
                self._market.search, keyword=cli_code, page_no=1, page_size=100
            )
        except (AcliMarketNotConfiguredError, AcliMarketUnavailableError):
            return None
        listed = next((tool for tool in page.items if tool.name == cli_code), None)
        if listed is not None:
            return listed
        alias_of = next((t.name for t in page.items if t.command == cli_code), None)
        if alias_of is not None:
            raise AcliCliNameMismatchError(cli_code, alias_of)
        return None

    def _kept_identity(self, bot: Mapping[str, Any], cli_code: str) -> str:
        """``caller`` if the owner set this tool to caller before, else ``owner``.

        A remove leaves the per-tool override row alone, so this is the
        identity the owner last chose for the tool.
        """
        overrides = self._identities.list_draft_cli_call_types(
            int(bot["id"]), str(bot["active_engine"])
        )
        return _mode(overrides.get(cli_code)) or "owner"

    def _default_codes(self, bot: Mapping[str, Any]) -> set[str]:
        template_type = bot.get("template_type")
        return {
            str(item["cli_code"])
            for item in self._manifest.required_cli_items(
                str(bot.get("active_engine") or ""),
                template_type if isinstance(template_type, str) else None,
            )
        }


def _mode(value: object) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value)).strip().lower() or None


#: Codes caused by the request itself rather than by the container.
_REJECTED_CODES = frozenset({"install_failed", "invalid_input", "name_mismatch"})


def _container_error(code: str, message: str) -> DomainError:
    """The domain error for an operation or port failure ``code``."""
    if code == "engine_unsupported":
        return AcliEngineUnsupportedError()
    if code == "not_running":
        return AcliBotNotRunningError(code, message)
    if code == "engine_outdated":
        return AcliEngineOutdatedError(code, message)
    if code == OperationStatus.CANCELLED:
        return AcliOperationCancelledError(code, message)
    if code == "not_active":
        return AcliOperationNotActiveError(message)
    if code == "grant_failed":
        return AcliGrantSyncError(code, message)
    if code in _REJECTED_CODES:
        return AcliRequestRejectedError(code, message)
    return AcliOperationFailedError(code, message)


__all__ = ["ACLI_ENGINES", "AcliCliService", "BotCli"]
