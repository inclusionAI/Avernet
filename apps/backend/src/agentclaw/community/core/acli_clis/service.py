"""List, search, add and remove a bot's acli market CLIs.

Two systems change together and neither has transactions: Passport (the
grant, the only record of what a bot has) and the bot's container (the
installed program). Each write therefore runs its two steps in a fixed order
and undoes the first when the second fails:

* **add** installs first, then grants. An unknown market name fails at the
  install, before Passport is touched; a failed grant uninstalls again.
* **remove** revokes first, then uninstalls. A failed uninstall re-grants.

If the undo fails too, :class:`AcliRollbackFailedError` names the state the
bot was left in, and the event is logged for repair.
"""
from __future__ import annotations

import asyncio
import time
import weakref
from collections.abc import AsyncIterator, Awaitable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from agentclaw.community.core.acli_clis.errors import (
    AcliCliAlreadyGrantedError,
    AcliCliNameMismatchError,
    AcliCliNotGrantedError,
    AcliBotNotRunningError,
    AcliDefaultCliError,
    AcliEngineUnsupportedError,
    AcliGrantSyncError,
    AcliOperationFailedError,
    AcliRequestRejectedError,
    AcliRollbackFailedError,
)
from agentclaw.community.core.acli_clis.market import AcliMarketSearch
from agentclaw.community.core.acli_clis.protocols import AcliCliServiceProtocol, BotCli
from agentclaw.community.core.acli_clis.port import (
    AcliMarketPage,
    AcliPort,
    AcliPortError,
    AcliTool,
)
from agentclaw.community.core.caller_identity.contracts import CallerLockEpochError
from agentclaw.community.core.caller_identity.guard import (
    load_bot,
    lock_epoch_matches,
    require_owner_of_active_service_bot,
)
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
from agentclaw.community.log import get_logger

logger = get_logger()

#: Engines whose containers ship acli and the ``/api/cli/acli/*`` endpoints.
ACLI_ENGINES = frozenset({"openclaw", "claude_code"})


class AcliCliService(AcliCliServiceProtocol):
    """Orchestrates Passport and the container; owns no policy of its own."""

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
    ) -> None:
        self._bots = bot_repository
        self._locks = lock_repository
        self._identities = identity_repository
        self._reconciler = scope_reconciler
        self._manifest = manifest_resolver
        self._port = port
        self._market = market
        # One add/remove at a time per bot: each spans Passport and container
        # steps with awaits between them, and the owner's edit lock is
        # reentrant, so two of the owner's requests could otherwise interleave.
        self._bot_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    # ── reads ────────────────────────────────────────────────────────────

    def list_bot_clis(self, *, bot_id: str, entity_id: str) -> list[BotCli]:
        """Every CLI in the bot's Passport grant, however it got there."""
        bot = load_bot(self._bots, bot_id=bot_id, entity_id=entity_id)
        defaults = self._default_codes(bot)
        overrides = self._identities.list_draft_cli_call_types(
            int(bot["id"]), str(bot["active_engine"])
        )
        items = self._reconciler.current_passport_cli_items(bot=bot)
        return [
            BotCli(
                cli_code=str(item["cli_code"]),
                cli_name=str(item.get("cli_name") or item["cli_code"]),
                cli_desc=str(item.get("cli_desc") or ""),
                identity_mode=_mode(overrides.get(str(item["cli_code"])))
                or str(item.get("identity_mode") or "owner"),
                default=str(item["cli_code"]) in defaults,
            )
            for item in items
        ]

    async def search_market(
        self, *, keyword: str, page_no: int, page_size: int
    ) -> AcliMarketPage:
        """One page of the acli market. The market is the same for every bot,
        so no bot or container is involved."""
        return await asyncio.to_thread(
            self._market.search, keyword=keyword, page_no=page_no, page_size=page_size
        )

    # ── writes ───────────────────────────────────────────────────────────

    async def add(
        self,
        *,
        bot_id: str,
        entity_id: str,
        actor_id: str,
        cli_code: str,
        lock_epoch: int | None,
    ) -> AcliTool:
        """Install, then grant; uninstall again if the grant fails."""
        async with self._serialized(bot_id):
            return await self._add(
                bot_id=bot_id, entity_id=entity_id, actor_id=actor_id,
                cli_code=cli_code, lock_epoch=lock_epoch,
            )

    async def _add(
        self,
        *,
        bot_id: str,
        entity_id: str,
        actor_id: str,
        cli_code: str,
        lock_epoch: int | None,
    ) -> AcliTool:
        started = time.monotonic()
        bot = self._writable_bot(bot_id, entity_id, actor_id, lock_epoch)
        owner_id = str(bot["owner_id"])
        granted_before = self._granted_codes(bot)
        if cli_code in granted_before:
            raise AcliCliAlreadyGrantedError(cli_code)
        # A tool removed earlier keeps the identity its owner chose, so a
        # re-add restores it. Read before installing, so a failed read
        # changes nothing.
        identity_mode = self._kept_identity(bot, cli_code)
        logger.info(
            "acli_cli_add_requested bot_id=%s cli_code=%s actor_id=%s", bot_id, cli_code, actor_id
        )

        try:
            tool = await self._port.install(
                bot_id=bot_id, owner_id=owner_id, name=cli_code,
            )
        except asyncio.CancelledError:
            # The request was abandoned (a worker shutting down, say) while the
            # engine may still finish the install. Treat it like any uncertain
            # install: clean up, shielded so the cleanup itself survives the
            # cancellation, then let the cancellation through.
            logger.warning(
                "acli_cli_add_failed bot_id=%s cli_code=%s phase=install error_code=cancelled",
                bot_id, cli_code,
            )
            await self._shielded(self._undo_install(
                bot_id, owner_id, cli_code,
                reason="install_cancelled", state="install_unknown_not_granted",
            ))
            raise
        except AcliPortError as error:
            logger.warning(
                "acli_cli_add_failed bot_id=%s cli_code=%s phase=install error_code=%s",
                bot_id, cli_code, error.code,
            )
            if error.code in _UNCERTAIN_CODES:
                # The install may have completed out of sight. Uninstall so the
                # bot cannot be left with an ungranted program; if that fails
                # too, the state is unknown and is reported as such.
                await self._undo_install(
                    bot_id, owner_id, cli_code,
                    reason="install_uncertain", state="install_unknown_not_granted",
                )
            raise _container_error(error) from error

        if tool.name != cli_code:
            # acli resolved an alias (``di`` → ``deepinsight-cli``). Passport
            # codes are market names, so undo and point the caller at it —
            # unless the bot already holds that tool, whose program must stay.
            # Judged against the pre-install snapshot: a fresh read here could
            # fail and strand the program the alias just installed.
            if tool.name not in granted_before:
                await self._undo_install(bot_id, owner_id, tool.name, reason="name_mismatch")
            raise AcliCliNameMismatchError(cli_code, tool.name)

        write_error: Exception | None = None
        try:
            self._reconciler.add_cli(
                bot=bot,
                item={
                    "cli_code": cli_code,
                    "cli_name": cli_code,
                    "cli_desc": tool.description,
                    "identity_mode": identity_mode,
                },
            )
        except Exception as error:
            logger.error(
                "acli_cli_add_failed bot_id=%s cli_code=%s phase=grant error_type=%s",
                bot_id, cli_code, type(error).__name__,
            )
            write_error = error
        # Read the grant back even after a clean write: a Passport can accept
        # a write it does not keep (the community self-issued one), or another
        # whole-scope write can overwrite it. Only a confirmed grant succeeds.
        if not self._passport_shows(
            bot, cli_code, granted=True, unknown_state="grant_unknown_installed",
            cause=write_error,
        ):
            await self._undo_install(
                bot_id, owner_id, cli_code,
                reason="grant_failed" if write_error else "grant_not_persisted",
            )
            raise AcliGrantSyncError from write_error

        logger.info(
            "acli_cli_add_succeeded bot_id=%s cli_code=%s version=%s duration_ms=%s",
            bot_id, cli_code, tool.version, int((time.monotonic() - started) * 1000),
        )
        return tool

    async def remove(
        self,
        *,
        bot_id: str,
        entity_id: str,
        actor_id: str,
        cli_code: str,
        lock_epoch: int | None,
    ) -> None:
        """Revoke, then uninstall; re-grant if the uninstall fails."""
        async with self._serialized(bot_id):
            await self._remove(
                bot_id=bot_id, entity_id=entity_id, actor_id=actor_id,
                cli_code=cli_code, lock_epoch=lock_epoch,
            )

    async def _remove(
        self,
        *,
        bot_id: str,
        entity_id: str,
        actor_id: str,
        cli_code: str,
        lock_epoch: int | None,
    ) -> None:
        """Revoke the grant, then uninstall the program; all or nothing.

        Refused up front: a tool the bot lacks (``AcliCliNotGrantedError``) and
        a default tool (``AcliDefaultCliError``). Then:

        * revoke fails → re-read Passport. Still granted → ``AcliGrantSyncError``
          (nothing changed); revoked after all → carry on; unreadable →
          ``revoke_unknown_installed``.
        * uninstall fails → re-grant with the same identity and return the
          container error; if that re-grant fails too → ``revoked_still_installed``
          (or ``revoke_unknown_installed`` when Passport cannot be read).
        * uninstall's answer never arrives (timeout, unreachable, bad output) →
          stay revoked, the safe side, and report ``revoked_install_unknown``.

        A state is reported as ``AcliRollbackFailedError`` (``500``, ``data.state``).
        """
        started = time.monotonic()
        bot = self._writable_bot(bot_id, entity_id, actor_id, lock_epoch)
        owner_id = str(bot["owner_id"])
        held = {
            str(item["cli_code"]): item
            for item in self._reconciler.current_passport_cli_items(bot=bot)
        }
        if cli_code not in held:
            raise AcliCliNotGrantedError(cli_code)
        if cli_code in self._default_codes(bot):
            raise AcliDefaultCliError(cli_code)
        logger.info(
            "acli_cli_remove_requested bot_id=%s cli_code=%s actor_id=%s",
            bot_id, cli_code, actor_id,
        )

        write_error: Exception | None = None
        try:
            self._reconciler.remove_cli(bot=bot, cli_code=cli_code)
        except Exception as error:
            logger.error(
                "acli_cli_remove_failed bot_id=%s cli_code=%s phase=revoke error_type=%s",
                bot_id, cli_code, type(error).__name__,
            )
            write_error = error
        # Confirm the revoke before the destructive step, even after a clean
        # write: uninstalling while Passport still grants the tool would leave
        # the bot authorized for a missing command. Nothing is uninstalled yet,
        # so an unreadable Passport leaves the grant in an unknown state.
        if not self._passport_shows(
            bot, cli_code, granted=False, unknown_state="revoke_unknown_installed",
            cause=write_error,
        ):
            raise AcliGrantSyncError from write_error

        try:
            await self._port.uninstall(
                bot_id=bot_id, owner_id=owner_id, name=cli_code,
            )
        except asyncio.CancelledError:
            # Abandoned mid-uninstall: whether the program is gone is unknown,
            # so stay revoked, the safe side, and record the state.
            logger.error(
                "acli_cli_rollback_failed bot_id=%s cli_code=%s "
                "state=revoked_install_unknown error_code=cancelled",
                bot_id, cli_code,
            )
            raise
        except AcliPortError as error:
            logger.warning(
                "acli_cli_remove_failed bot_id=%s cli_code=%s phase=uninstall error_code=%s",
                bot_id, cli_code, error.code,
            )
            if error.code in _UNCERTAIN_CODES:
                # The uninstall may have completed out of sight. Re-granting
                # could authorize a tool that is gone, so stay revoked — the
                # safe side — and report that the program's state is unknown.
                logger.error(
                    "acli_cli_rollback_failed bot_id=%s cli_code=%s "
                    "state=revoked_install_unknown error_code=%s",
                    bot_id, cli_code, error.code,
                )
                raise AcliRollbackFailedError(cli_code, "revoked_install_unknown") from error
            self._undo_revoke(bot, cli_code, held[cli_code])
            raise _container_error(error) from error

        logger.info(
            "acli_cli_remove_succeeded bot_id=%s cli_code=%s duration_ms=%s",
            bot_id, cli_code, int((time.monotonic() - started) * 1000),
        )

    @asynccontextmanager
    async def _serialized(self, bot_id: str) -> AsyncIterator[None]:
        # Held locally while waiting or held, so the weak entry lives exactly
        # as long as some request for this bot does.
        lock = self._bot_locks.get(bot_id)
        if lock is None:
            lock = asyncio.Lock()
            self._bot_locks[bot_id] = lock
        async with lock:
            yield

    @staticmethod
    async def _shielded(cleanup: Awaitable[None]) -> None:
        """Run ``cleanup`` to completion even though the caller was cancelled.

        A failed cleanup is already logged with its state by the cleanup
        itself; the cancellation, not that error, is what the caller sees.
        """
        try:
            await asyncio.shield(cleanup)
        except (AcliRollbackFailedError, asyncio.CancelledError):
            pass

    # ── rollback ─────────────────────────────────────────────────────────

    async def _undo_install(
        self,
        bot_id: str,
        owner_id: str,
        name: str,
        *,
        reason: str,
        state: str = "installed_not_granted",
    ) -> None:
        try:
            await self._port.uninstall(
                bot_id=bot_id, owner_id=owner_id, name=name,
            )
        except AcliPortError as error:
            logger.error(
                "acli_cli_rollback_failed bot_id=%s cli_code=%s state=%s "
                "reason=%s error_code=%s",
                bot_id, name, state, reason, error.code,
            )
            raise AcliRollbackFailedError(name, state) from error

    def _undo_revoke(self, bot: Mapping[str, Any], cli_code: str, item: Mapping[str, Any]) -> None:
        """Re-grant ``item`` after a failed uninstall, so the bot is as before the remove.

        The caller/owner override lives in its own table and the revoke did not
        touch it, so re-granting restores the same identity.
        """
        write_error: Exception | None = None
        try:
            self._reconciler.add_cli(bot=bot, item=item)
        except Exception as error:
            write_error = error
        # Read back after a clean write too: a re-grant Passport did not keep
        # leaves the bot revoked with its program installed.
        if not self._passport_shows(
            bot, cli_code, granted=True, unknown_state="revoke_unknown_installed",
            cause=write_error,
        ):
            logger.error(
                "acli_cli_rollback_failed bot_id=%s cli_code=%s "
                "state=revoked_still_installed error_type=%s",
                bot["bot_id"], cli_code, type(write_error).__name__ if write_error else None,
            )
            raise AcliRollbackFailedError(cli_code, "revoked_still_installed") from write_error

    # ── shared checks ────────────────────────────────────────────────────

    def _writable_bot(
        self, bot_id: str, entity_id: str, actor_id: str, lock_epoch: int | None
    ) -> Mapping[str, Any]:
        """The bot, if ``actor_id`` may change its CLIs right now.

        It must exist for ``entity_id``, be an active service bot, be owned by
        ``actor_id``, run an engine that has acli, and ``lock_epoch`` must name
        the edit lock ``actor_id`` holds (or be ``None`` while no lock exists).
        The edit lock decides who may edit; it does not serialize one holder's
        concurrent requests (see ``_serialized``).
        """
        bot = load_bot(self._bots, bot_id=bot_id, entity_id=entity_id)
        require_owner_of_active_service_bot(bot, actor_id)
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

    def _kept_identity(self, bot: Mapping[str, Any], cli_code: str) -> str:
        """``caller`` if the owner set this tool to caller before, else ``owner``.

        A remove leaves the per-tool override row alone, so this is the
        identity the owner last chose for the tool.
        """
        overrides = self._identities.list_draft_cli_call_types(
            int(bot["id"]), str(bot["active_engine"])
        )
        return _mode(overrides.get(cli_code)) or "owner"

    def _passport_shows(
        self,
        bot: Mapping[str, Any],
        cli_code: str,
        *,
        granted: bool,
        unknown_state: str,
        cause: Exception | None,
    ) -> bool:
        """Re-read Passport after a write: does it show ``cli_code`` as ``granted``?

        Every grant or revoke ends here, whether its write raised or not, so
        the outcome always follows what Passport actually holds rather than
        what the write call reported. When Passport cannot be read, nothing
        safe can be undone, so this raises :class:`AcliRollbackFailedError`
        naming ``unknown_state``.
        """
        state = self._grant_state(bot, cli_code)
        if state is None:
            logger.error(
                "acli_cli_rollback_failed bot_id=%s cli_code=%s state=%s",
                bot["bot_id"], cli_code, unknown_state,
            )
            raise AcliRollbackFailedError(cli_code, unknown_state) from cause
        if cause is not None and state is granted:
            logger.info(
                "acli_cli_passport_write_landed_despite_error bot_id=%s cli_code=%s granted=%s",
                bot["bot_id"], cli_code, granted,
            )
        return state is granted

    def _grant_state(self, bot: Mapping[str, Any], cli_code: str) -> bool | None:
        """Whether Passport now grants ``cli_code``; ``None`` when it cannot say."""
        try:
            return cli_code in self._granted_codes(bot)
        except Exception as error:
            logger.warning(
                "acli_cli_grant_recheck_failed bot_id=%s cli_code=%s error_type=%s",
                bot["bot_id"], cli_code, type(error).__name__,
            )
            return None

    def _granted_codes(self, bot: Mapping[str, Any]) -> set[str]:
        return {
            str(item["cli_code"]) for item in self._reconciler.current_passport_cli_items(bot=bot)
        }

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


#: Port codes after which the container may or may not have done the work.
_UNCERTAIN_CODES = frozenset({"timeout", "engine_unreachable", "bad_output"})
#: Port codes caused by the request itself rather than by the container.
_REJECTED_CODES = frozenset({"install_failed", "invalid_input"})


def _container_error(error: AcliPortError) -> DomainError:
    if error.code == "engine_unsupported":
        return AcliEngineUnsupportedError()
    if error.code == "not_running":
        return AcliBotNotRunningError(error.code, error.message)
    if error.code in _REJECTED_CODES:
        return AcliRequestRejectedError(error.code, error.message)
    return AcliOperationFailedError(error.code, error.message)


__all__ = ["ACLI_ENGINES", "AcliCliService", "BotCli"]
