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

import time
from collections.abc import Mapping
from dataclasses import dataclass
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
from agentclaw.community.core.acli_clis.port import (
    AcliMarketPage,
    AcliPort,
    AcliPortError,
    AcliTool,
)
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityIrreversibleError,
    CallerLockEpochError,
)
from agentclaw.community.core.caller_identity.guard import (
    load_bot,
    lock_epoch_matches,
    require_owner_of_active_service_bot,
)
from agentclaw.community.core.caller_identity.models import McpCallType
from agentclaw.community.core.errors import DomainError
from agentclaw.community.core.mcp.services.cli_capabilities import (
    CliCapabilityManifestResolver,
)
from agentclaw.community.core.mcp.services.cli_passport_scope import (
    CliPassportScopeReconciler,
)
from agentclaw.community.core.mcp.services.repositories import BotMCPProvider
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


@dataclass(frozen=True, slots=True)
class BotCli:
    """One CLI a bot holds in Passport."""

    cli_code: str
    cli_name: str
    cli_desc: str
    identity_mode: str
    default: bool


class AcliCliService:
    """Orchestrates Passport and the container; owns no policy of its own."""

    def __init__(
        self,
        *,
        bot_repository: BotRepository,
        lock_repository: BotCollabLockRepositoryProtocol,
        identity_repository: CallerIdentityRepositoryProtocol,
        mcp_provider: BotMCPProvider,
        scope_reconciler: CliPassportScopeReconciler,
        manifest_resolver: CliCapabilityManifestResolver,
        port: AcliPort,
    ) -> None:
        self._bots = bot_repository
        self._locks = lock_repository
        self._identities = identity_repository
        self._mcp_provider = mcp_provider
        self._reconciler = scope_reconciler
        self._manifest = manifest_resolver
        self._port = port

    # ── reads ────────────────────────────────────────────────────────────

    def list_bot_clis(self, *, bot_id: str, entity_id: str | None) -> list[BotCli]:
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
        self,
        *,
        bot_id: str,
        entity_id: str | None,
        keyword: str,
        page_no: int,
        page_size: int,
    ) -> AcliMarketPage:
        """One page of the acli market, searched from inside the bot's container."""
        bot = load_bot(self._bots, bot_id=bot_id, entity_id=entity_id)
        self._require_acli_engine(bot)
        try:
            return await self._port.market(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                keyword=keyword,
                page_no=page_no,
                page_size=page_size,
            )
        except AcliPortError as error:
            raise _container_error(error) from error

    # ── writes ───────────────────────────────────────────────────────────

    async def add(
        self,
        *,
        bot_id: str,
        entity_id: str | None,
        actor_id: str,
        cli_code: str,
        lock_epoch: int | None,
    ) -> AcliTool:
        """Install, then grant; uninstall again if the grant fails."""
        started = time.monotonic()
        bot = self._writable_bot(bot_id, entity_id, actor_id, lock_epoch)
        owner_id = str(bot["owner_id"])
        if cli_code in self._granted_codes(bot):
            raise AcliCliAlreadyGrantedError(cli_code)
        logger.info(
            "acli_cli_add_requested bot_id=%s cli_code=%s actor_id=%s", bot_id, cli_code, actor_id
        )

        try:
            tool = await self._port.install(
                bot_id=bot_id, owner_id=owner_id, name=cli_code,
                install_dir=self._manifest.managed_bin_dir,
            )
        except AcliPortError as error:
            logger.warning(
                "acli_cli_add_failed bot_id=%s cli_code=%s phase=install error_code=%s",
                bot_id, cli_code, error.code,
            )
            raise _container_error(error) from error

        if tool.name != cli_code:
            # acli resolved an alias (``di`` → ``deepinsight-cli``). Passport
            # codes are market names, so undo and point the caller at it —
            # unless the bot already holds that tool, whose program must stay.
            if tool.name not in self._granted_codes(bot):
                await self._undo_install(bot_id, owner_id, tool.name, reason="name_mismatch")
            raise AcliCliNameMismatchError(cli_code, tool.name)

        try:
            self._reconciler.add_cli(
                bot=bot,
                item={
                    "cli_code": cli_code,
                    "cli_name": cli_code,
                    "cli_desc": tool.description,
                    "identity_mode": "owner",
                },
            )
        except Exception as error:
            logger.error(
                "acli_cli_add_failed bot_id=%s cli_code=%s phase=grant error_type=%s",
                bot_id, cli_code, type(error).__name__,
            )
            if self._grant_landed(bot, cli_code):
                # The write failed on the way back (a timeout, say) after
                # Passport had applied it: the add actually succeeded.
                logger.info(
                    "acli_cli_add_succeeded bot_id=%s cli_code=%s grant=landed_despite_error",
                    bot_id, cli_code,
                )
                return tool
            await self._undo_install(bot_id, owner_id, cli_code, reason="grant_failed")
            raise AcliGrantSyncError from error

        logger.info(
            "acli_cli_add_succeeded bot_id=%s cli_code=%s version=%s duration_ms=%s",
            bot_id, cli_code, tool.version, int((time.monotonic() - started) * 1000),
        )
        return tool

    async def remove(
        self,
        *,
        bot_id: str,
        entity_id: str | None,
        actor_id: str,
        cli_code: str,
        lock_epoch: int | None,
    ) -> None:
        """Revoke, then uninstall; re-grant if the uninstall fails."""
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

        try:
            self._reconciler.remove_cli(bot=bot, cli_code=cli_code)
        except Exception as error:
            logger.error(
                "acli_cli_remove_failed bot_id=%s cli_code=%s phase=revoke error_type=%s",
                bot_id, cli_code, type(error).__name__,
            )
            raise AcliGrantSyncError from error

        try:
            await self._port.uninstall(
                bot_id=bot_id, owner_id=owner_id, name=cli_code,
                install_dir=self._manifest.managed_bin_dir,
            )
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

        self._clear_identity_override(bot, cli_code, actor_id, lock_epoch, set(held) - {cli_code})
        logger.info(
            "acli_cli_remove_succeeded bot_id=%s cli_code=%s duration_ms=%s",
            bot_id, cli_code, int((time.monotonic() - started) * 1000),
        )

    # ── rollback ─────────────────────────────────────────────────────────

    async def _undo_install(self, bot_id: str, owner_id: str, name: str, *, reason: str) -> None:
        try:
            await self._port.uninstall(
                bot_id=bot_id, owner_id=owner_id, name=name,
                install_dir=self._manifest.managed_bin_dir,
            )
        except AcliPortError as error:
            logger.error(
                "acli_cli_rollback_failed bot_id=%s cli_code=%s state=installed_not_granted "
                "reason=%s error_code=%s",
                bot_id, name, reason, error.code,
            )
            raise AcliRollbackFailedError(name, "installed_not_granted") from error

    def _undo_revoke(self, bot: Mapping[str, Any], cli_code: str, item: Mapping[str, Any]) -> None:
        # The caller/owner override lives in its own table and the revoke did
        # not touch it, so re-granting restores the same identity.
        try:
            self._reconciler.add_cli(bot=bot, item=item)
        except Exception as error:
            logger.error(
                "acli_cli_rollback_failed bot_id=%s cli_code=%s state=revoked_still_installed "
                "error_type=%s",
                bot["bot_id"], cli_code, type(error).__name__,
            )
            raise AcliRollbackFailedError(cli_code, "revoked_still_installed") from error

    def _clear_identity_override(
        self,
        bot: Mapping[str, Any],
        cli_code: str,
        actor_id: str,
        lock_epoch: int | None,
        remaining_cli_codes: set[str],
    ) -> None:
        """Drop a removed CLI's caller override so a later re-add starts at owner.

        Best effort, after the removal has already succeeded: the platform does
        not let a bot fall back from ``caller`` to ``owner``, so when this CLI
        was the bot's only caller resource the override is kept and the event
        logged. A re-add then comes back as ``caller``, as the bot already is.
        """
        engine_type = str(bot["active_engine"])
        overrides = self._identities.list_draft_cli_call_types(int(bot["id"]), engine_type)
        if _mode(overrides.get(cli_code)) != "caller":
            return
        try:
            active_mcps = self._mcp_provider.collect_bot_active_mcps(
                entity_id=str(bot["entity_id"]),
                bot_id=str(bot["bot_id"]),
                user_id=actor_id,
                entity_type=str(bot.get("entity_type") or "staff"),
                engine_type=engine_type,
            )
            self._identities.replace_draft_cli_call_type(
                bot_pk=int(bot["id"]),
                engine_type=engine_type,
                cli_code=cli_code,
                call_type=McpCallType.OWNER,
                modifier_id=actor_id,
                lock_key=f"{bot['bot_id']}:{actor_id}",
                lock_holder_user_id=actor_id,
                lock_epoch=lock_epoch,
                effective_server_codes={
                    str(item["server_code"])
                    for item in active_mcps
                    if isinstance(item, Mapping) and item.get("server_code")
                },
                effective_cli_codes=remaining_cli_codes,
            )
        except CallerIdentityIrreversibleError:
            logger.info(
                "acli_cli_override_kept bot_id=%s cli_code=%s reason=caller_irreversible",
                bot["bot_id"], cli_code,
            )
        except Exception as error:
            logger.warning(
                "acli_cli_override_clear_failed bot_id=%s cli_code=%s error_type=%s",
                bot["bot_id"], cli_code, type(error).__name__,
            )

    # ── shared checks ────────────────────────────────────────────────────

    def _writable_bot(
        self, bot_id: str, entity_id: str | None, actor_id: str, lock_epoch: int | None
    ) -> Mapping[str, Any]:
        bot = load_bot(self._bots, bot_id=bot_id, entity_id=entity_id)
        require_owner_of_active_service_bot(bot, actor_id)
        self._require_acli_engine(bot)
        if not lock_epoch_matches(
            self._locks, bot_id=bot_id, actor_id=actor_id, lock_epoch=lock_epoch
        ):
            raise CallerLockEpochError
        return bot

    @staticmethod
    def _require_acli_engine(bot: Mapping[str, Any]) -> None:
        if str(bot.get("active_engine") or "") not in ACLI_ENGINES:
            raise AcliEngineUnsupportedError

    def _grant_landed(self, bot: Mapping[str, Any], cli_code: str) -> bool:
        try:
            return cli_code in self._granted_codes(bot)
        except Exception as error:
            logger.warning(
                "acli_cli_grant_recheck_failed bot_id=%s cli_code=%s error_type=%s",
                bot["bot_id"], cli_code, type(error).__name__,
            )
            return False

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
_UNCERTAIN_CODES = frozenset({"timeout", "engine_unreachable"})
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
