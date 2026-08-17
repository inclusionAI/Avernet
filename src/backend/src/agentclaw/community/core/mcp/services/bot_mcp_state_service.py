"""Add, activate, deactivate and remove MCP servers on one bot.

This is the `skills` mechanism applied to MCP. ``LocalSkillStateService`` has
been running it in production for skills, and every piece it needs already
existed on the MCP side — see ``specs/2026-08-17-openapi-v1-mcp-bot-scoped/
plan.md`` for the derivation. Two of its decisions are worth repeating here,
because both look wrong until you know why:

**The container is the bot's *default* skill set, and it has to be.**
Skill-set activation is exclusive: ``set_active_skill_set`` clears
``is_active`` on every non-default set for the (user, bot, engine) before
activating its target, filtering on nothing that could exempt one. A skill set
owned by this surface — one, or one per server — would therefore be switched
off the moment anyone changed skill sets in the workbench, taking every MCP on
it down with no signal. The default set is appended separately by
``get_all_active_skill_sets`` and never swept.

**Activation is an exclusion row, not a flag.** Membership in the default set
says the server is *on the bot*; a row in ``ac_default_skillset_mcp_exclusion``
says it is off. Exactly what ``_write_desired_state`` does for skills. No
column and no table were added for this.

The word "skill set" appears nowhere on the public contract. It is storage.
"""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.core.mcp.errors import (
    McpBotServerNotFoundError,
    McpDefaultServerNotRemovableError,
    McpServerNotFoundError,
    McpSyncFailedError,
)
from agentclaw.community.core.mcp.presentation import is_network_type_visible
from agentclaw.community.core.mcp.services._defaults import get_default_mcp_servers
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.mcp.services.sync_service import MCPSyncService
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillSetRepository,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin

logger = get_logger()

#: Every bot this surface reaches is personally owned — ``openapi_v1`` is a
#: personal-bot surface throughout, and ``proj``/``team`` are out of scope for
#: it by the same decision the identity group records.
_ENTITY_TYPE = "staff"


class BotMcpStateService:
    """Authorize, mutate desired state, then synchronously reconcile runtime."""

    @inject
    def __init__(
        self,
        skill_set_repo: SkillSetRepository,
        bot_repo: BotRepository,
        mcp_center: MCPCenterPlugin,
        sync_service: MCPSyncService,
    ) -> None:
        self._skill_set_repo = skill_set_repo
        self._bot_repo = bot_repo
        self._mcp_center = mcp_center
        self._sync_service = sync_service

    # ── resolution ──────────────────────────────────────────────────

    def _resolve(self, bot_id: str, owner_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return ``(bot, default_set)`` or raise the masked not-found.

        A bot the caller does not own is indistinguishable from one that does
        not exist — ``get_by_id_and_owner`` is the whole authorization, exactly
        as in the other user-scoped groups.

        A missing default set is *also* not-found rather than an implicit
        create, mirroring ``local_skill_state_service``: inventing a skill set
        as a side effect of an MCP call would put a bot into a state nothing
        else on this surface can explain.
        """
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise McpBotServerNotFoundError(bot_id)
        default_set = self._skill_set_repo.get_default(
            user_id=owner_id,
            bolt_id=bot_id,
            engine_type=bot.get("active_engine"),
        )
        if default_set is None:
            logger.warning(
                "[BotMcpStateService] no default skill set for bot=%s owner=%s",
                bot_id,
                owner_id,
            )
            raise McpBotServerNotFoundError(bot_id)
        return bot, default_set

    def _defaults_for(self, bot: dict[str, Any], bot_id: str) -> list[dict[str, Any]]:
        """The engine-supplied MCP servers for this bot, as plain dicts."""
        try:
            return list(get_default_mcp_servers(bot.get("active_engine")) or [])
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "[BotMcpStateService] default MCP lookup failed bot=%s: %s",
                bot_id,
                exc,
            )
            return []

    def _merged(
        self, *, bot: dict[str, Any], bot_id: str, owner_id: str, default_set: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Every MCP on this bot — stored rows and engine defaults — with state.

        ``active`` is "not excluded", read through ``get_all_excluded_mcps``
        (every default set, not just the current one) because that is the read
        the runtime collection uses. Reading it per-set here would let a
        stranded exclusion show as active while the agent could not call it.

        A code that is both a stored row and an engine default is reported once,
        as the stored row: it is the one an operation can act on.
        """
        excluded = set(self._skill_set_repo.get_all_excluded_mcps(owner_id, bot_id))
        merged: dict[str, dict[str, Any]] = {}

        for row in self._skill_set_repo.get_mcp_servers_in_set(str(default_set["id"])):
            code = row.get("server_code")
            if not code:
                continue
            merged[code] = {
                "server_code": code,
                "name": row.get("name") or code,
                "description": row.get("description"),
                "is_default": False,
                "active": code not in excluded,
            }

        for cfg in self._defaults_for(bot, bot_id):
            code = cfg.get("server_code")
            if not code or code in merged:
                continue
            merged[code] = {
                "server_code": code,
                "name": cfg.get("name") or code,
                "description": cfg.get("description"),
                "is_default": True,
                "active": code not in excluded,
            }

        return list(merged.values())

    # ── reads ───────────────────────────────────────────────────────

    def list_bot_servers(self, *, bot_id: str, owner_id: str) -> list[dict[str, Any]]:
        """Every MCP server on this bot, each with its active state."""
        bot, default_set = self._resolve(bot_id, owner_id)
        return self._merged(
            bot=bot, bot_id=bot_id, owner_id=owner_id, default_set=default_set
        )

    def get_bot_server(
        self, *, bot_id: str, owner_id: str, server_code: str
    ) -> dict[str, Any]:
        """One server's state on this bot, or not-found if it is not on it."""
        for entry in self.list_bot_servers(bot_id=bot_id, owner_id=owner_id):
            if entry["server_code"] == server_code:
                return entry
        raise McpBotServerNotFoundError(server_code)

    # ── reconciliation ──────────────────────────────────────────────

    async def _reconcile(self, *, bot: dict[str, Any], bot_id: str, owner_id: str) -> None:
        """Declare the bot's MCP whitelist to its device, or raise.

        ``refresh_mcp_scope`` is the operation whose own docstring names this
        caller ("skill set 切换、激活/取消激活后调用"). Every mutation ends here,
        and a failure is the caller's failure — the caller must never be told a
        server is active while the agent cannot call it.
        """
        result = await self._sync_service.refresh_mcp_scope(
            user_id=owner_id,
            entity_id=owner_id,
            bot_id=bot_id,
            entity_type=_ENTITY_TYPE,
            engine_type=bot.get("active_engine"),
        )
        if not result or not result.get("success"):
            raise McpSyncFailedError(
                (result or {}).get("error") or "Failed to refresh MCP scope"
            )

    # ── mutations ───────────────────────────────────────────────────

    async def add_bot_server(
        self, *, bot_id: str, owner_id: str, server_code: str
    ) -> dict[str, Any]:
        """Put a marketplace server on this bot, **deactivated**.

        Adding never changes what the agent can call — that takes an explicit
        activate. Idempotent: a server already on the bot reports
        ``changed: false`` and its current state, rather than erroring or
        duplicating the row.

        The membership write goes through the repository, not
        ``SkillSetService.add_mcp_to_skill_set``, because that method refuses
        the default set outright. Skills does the same thing for the same
        reason (``_ensure_default_set_membership`` calls ``add_skill_to_set``).
        """
        bot, default_set = self._resolve(bot_id, owner_id)

        existing = {
            e["server_code"]
            for e in self._merged(
                bot=bot, bot_id=bot_id, owner_id=owner_id, default_set=default_set
            )
        }
        if server_code in existing:
            return {
                "server": self.get_bot_server(
                    bot_id=bot_id, owner_id=owner_id, server_code=server_code
                ),
                "changed": False,
            }

        detail = self._mcp_center.get_mcp_detail(server_code)
        if not detail or not is_network_type_visible(detail):
            # One raise site, so a server hidden by the network-type rule is
            # indistinguishable from one that does not exist.
            raise McpServerNotFoundError(server_code)

        set_id = str(default_set["id"])
        added = self._skill_set_repo.add_mcp_to_set(
            set_id,
            server_code,
            detail.get("name") or server_code,
            description=detail.get("description"),
            icon=detail.get("icon"),
            user_id=owner_id,
        )
        if not added:
            raise McpBotServerNotFoundError(bot_id)

        # Land inactive. Written *after* the membership row so the two can never
        # be observed in the order that would briefly expose an active server.
        self._skill_set_repo.add_default_mcp_exclusion(
            user_id=owner_id,
            bot_id=bot_id,
            skill_set_id=int(default_set["id"]),
            server_code=server_code,
        )

        try:
            await self._reconcile(bot=bot, bot_id=bot_id, owner_id=owner_id)
        except McpSyncFailedError:
            self._skill_set_repo.remove_mcp_from_set(set_id, server_code)
            self._skill_set_repo.remove_all_default_mcp_exclusions(
                owner_id, bot_id, server_code
            )
            raise

        return {
            "server": self.get_bot_server(
                bot_id=bot_id, owner_id=owner_id, server_code=server_code
            ),
            "changed": True,
        }

    async def set_bot_server_active(
        self, *, bot_id: str, owner_id: str, server_code: str, active: bool
    ) -> dict[str, Any]:
        """Turn a server on or off for this bot.

        Idempotent — acting on a server already in the requested state succeeds
        with ``changed: false`` — and identical for stored rows and engine
        defaults, because both are gated by the same exclusion rows. A server
        that is not on the bot at all is not-found rather than silently added.
        """
        bot, default_set = self._resolve(bot_id, owner_id)
        before = self.get_bot_server(
            bot_id=bot_id, owner_id=owner_id, server_code=server_code
        )
        if bool(before["active"]) == active:
            return {"server": before, "changed": False}

        if active:
            # Across every default set, not just the current one — an exclusion
            # stranded on a former default set would otherwise keep the server
            # off while this reported success.
            self._skill_set_repo.remove_all_default_mcp_exclusions(
                owner_id, bot_id, server_code
            )
        else:
            self._skill_set_repo.add_default_mcp_exclusion(
                user_id=owner_id,
                bot_id=bot_id,
                skill_set_id=int(default_set["id"]),
                server_code=server_code,
            )

        try:
            await self._reconcile(bot=bot, bot_id=bot_id, owner_id=owner_id)
        except McpSyncFailedError:
            # Put the desired state back the way it was.
            if active:
                self._skill_set_repo.add_default_mcp_exclusion(
                    user_id=owner_id,
                    bot_id=bot_id,
                    skill_set_id=int(default_set["id"]),
                    server_code=server_code,
                )
            else:
                self._skill_set_repo.remove_all_default_mcp_exclusions(
                    owner_id, bot_id, server_code
                )
            raise

        return {
            "server": self.get_bot_server(
                bot_id=bot_id, owner_id=owner_id, server_code=server_code
            ),
            "changed": True,
        }

    async def remove_bot_server(
        self, *, bot_id: str, owner_id: str, server_code: str
    ) -> bool:
        """Take a server off this bot entirely.

        Returns whether anything was removed; a server the bot does not have is
        ``False``, not an error.

        An engine default is refused: it is synthesised per request rather than
        stored, so there is no row to delete and "not on the bot" is not a state
        it can hold. Deactivating is the operation that means what the caller
        wants.

        The caller's stored credential is untouched — it is account state keyed
        by ``(user_id, server_code)`` and outlives any one bot.
        """
        bot, default_set = self._resolve(bot_id, owner_id)
        entries = {
            e["server_code"]: e
            for e in self._merged(
                bot=bot, bot_id=bot_id, owner_id=owner_id, default_set=default_set
            )
        }
        entry = entries.get(server_code)
        if entry is None:
            return False
        if entry["is_default"]:
            raise McpDefaultServerNotRemovableError(server_code)

        set_id = str(default_set["id"])
        # Captured before the delete so a rollback restores the row as it was,
        # not as ``_merged`` projected it — the projection drops ``icon``, and
        # restoring from it would quietly lose the field on every failed remove.
        stored = next(
            (
                row
                for row in self._skill_set_repo.get_mcp_servers_in_set(set_id)
                if row.get("server_code") == server_code
            ),
            {},
        )
        was_inactive = not entry["active"]

        self._skill_set_repo.remove_mcp_from_set(set_id, server_code)
        # Clear the exclusion too, or a later re-add would come back off for a
        # reason the caller has no way to see.
        self._skill_set_repo.remove_all_default_mcp_exclusions(
            owner_id, bot_id, server_code
        )

        try:
            await self._reconcile(bot=bot, bot_id=bot_id, owner_id=owner_id)
        except McpSyncFailedError:
            # Same contract as the other two mutations: a reconcile failure must
            # not leave stored state diverged from the device. Without this the
            # row is already gone, the caller gets a 502, and a retry answers
            # "nothing to remove" — silently masking a real unsynced mutation.
            #
            # Each repository call commits in its own session, so this is a
            # compensating write rather than a transaction rollback: put the
            # membership back, and its exclusion with it if the server was
            # inactive, so the bot returns to exactly the state it was in.
            self._skill_set_repo.add_mcp_to_set(
                set_id,
                server_code,
                stored.get("name") or entry["name"],
                description=stored.get("description") or entry.get("description"),
                icon=stored.get("icon"),
                user_id=owner_id,
            )
            if was_inactive:
                self._skill_set_repo.add_default_mcp_exclusion(
                    user_id=owner_id,
                    bot_id=bot_id,
                    skill_set_id=int(default_set["id"]),
                    server_code=server_code,
                )
            raise

        return True
