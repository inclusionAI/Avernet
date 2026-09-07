"""``BotCliToolService`` — the CLI-tools surface addressed by ``bot_id``.

What the HTTP routes call. It resolves one bot's storage coordinates and engine
family, refuses a bot whose engine takes no CLI tools, and hands the work to
:class:`CliToolService` — the same component the manifest's ``cli_tools``
materialiser calls. It implements no step of the install itself, which is the
whole point: the API and a manifest apply must refuse the same declaration for
the same reason, and they can only do that by sharing the code that decides.

**The bot lookup is the ownership guard as well as the address.**
``bot_service.get_bot(bot_id, owner_id)`` resolves the bot only for the named
owner, so a bot that is not theirs is indistinguishable from one that does not
exist — and the record the coordinates are read off is the record the guard
passed. Check and address cannot disagree, because there is one lookup.

**The capability answer is re-asked here, not trusted from the manifest's
``PUT``.** A bot's engine can change, and this surface has no stored document
to have been validated against in the first place.

**This service does not know what teclaw is.** A teclaw mutation still has to
reach the running container — writing the row and the bytes changes nothing a
container can see, since on that family the composed artifact *is* the
delivery — but that is the delivery port's job, and rev 8 moved it there. The
port this service's `CliToolService` holds is the one bound for *this* path,
which carries the redeliver; the apply path's is bound without it, because a
manifest apply closes with `TeclawDelivery.finish` instead. Neither branch is
taken here.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Protocol, Sequence

from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    capabilities_for_bot,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.context import (
    CliToolContext,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.declarations import (
    CliToolDecl,
    CliToolStatus,
    CliToolOutcome,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.models import (
    BotCliToolRecord,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.service import (
    CliToolService,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.service_protocol import (
    BotCliToolServiceProtocol,
    CliToolConflictError,
    CliToolNotFoundError,
    CliToolRefusedError,
    CliToolUnsupportedError,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

#: The delivery bindings, as the service factory keys them. Not quite "engine
#: family": teclaw has two, because *who pushes the artifact* differs by caller
#: and not by engine. This surface is the live one, so its binding carries the
#: redeliver; a manifest apply asks for plain ``"teclaw"`` and closes with
#: ``TeclawDelivery.finish`` instead (spec D-14).
FAMILY_TECLAW = "teclaw-live"
FAMILY_ARCA = "arca"


class BotLookupPort(Protocol):
    """``BotServiceProtocol.get_bot``, as a type key.

    Named rather than imported for the reason ``core/ports/resource_file_port.py``
    records: the concrete bot service reaches the device graph at import time,
    and this package is imported by the DI graph that builds it.
    """

    def get_bot(self, bot_id: str, owner_id: str) -> dict: ...


class BotCliToolService(BotCliToolServiceProtocol):
    """Resolve the bot, pick the family, delegate. Nothing else."""

    def __init__(
        self,
        *,
        bot_service: BotLookupPort,
        cli_tool_service_factory: Callable[[str], CliToolService],
        is_teclaw: Callable[[Optional[str]], bool],
    ) -> None:
        self._bots = bot_service
        self._factory = cli_tool_service_factory
        # Still needed, and only for the *capability* answer below: whether a
        # bot's engine supports this category at all. Not for delivery — that
        # moved into the port at rev 8.
        self._is_teclaw = is_teclaw

    # ── the surface ──────────────────────────────────────────────────────

    async def install(
        self, *, bot_id: str, owner_id: str, actor_id: str, decl: CliToolDecl
    ) -> BotCliToolRecord:
        service, ctx = self._resolve(bot_id, owner_id, actor_id)
        conflict = CliToolConflictError(
            f"bot {bot_id} already has a CLI tool named {decl.name!r}; "
            "remove it first, or declare it in the bot's manifest, which "
            "replaces the whole set"
        )
        # Asked twice, and both are needed. This read is the *cheap* answer —
        # it refuses before a fetch that could take minutes and hundreds of
        # megabytes. It is not the authoritative one: the name can be taken
        # during that fetch, so the write itself is an insert whose UNIQUE
        # constraint decides. 409 rather than a silent replacement either way —
        # a manifest apply *does* replace, because a full override is its
        # declared semantics, but a single POST is not, and overwriting a tool
        # the caller did not mention would be the surprising reading of
        # "install".
        if service.get(ctx, decl.name) is not None:
            raise conflict
        outcome = await service.install(
            ctx, decl, installed_by=actor_id, expect_absent=True
        )
        if outcome.status is CliToolStatus.CONFLICT:
            raise conflict
        if outcome.failed or outcome.record is None:
            raise CliToolRefusedError(outcome.detail or "the tool could not be installed")
        return outcome.record

    def list(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> Sequence[BotCliToolRecord]:
        service, ctx = self._resolve(bot_id, owner_id, actor_id, require_support=False)
        return service.list(ctx)

    async def remove(
        self, *, bot_id: str, owner_id: str, actor_id: str, name: str
    ) -> CliToolOutcome:
        service, ctx = self._resolve(bot_id, owner_id, actor_id, require_support=False)
        if service.get(ctx, name) is None:
            raise CliToolNotFoundError(f"bot {bot_id} has no CLI tool named {name!r}")
        outcome = await service.remove(ctx, name)
        if outcome.status is not CliToolStatus.REMOVED:
            raise CliToolRefusedError(outcome.detail or "the tool could not be removed")
        return outcome

    # ── resolution ───────────────────────────────────────────────────────

    def _resolve(
        self, bot_id: str, owner_id: str, actor_id: str, *, require_support: bool = True
    ) -> tuple[CliToolService, CliToolContext]:
        """The bot's family-bound service and its context, in one lookup.

        ``require_support`` is off for the reads and for removal: a bot whose
        engine changed to one that takes no CLI tools still has rows, and
        refusing to list or clean them up would strand them. Only *adding* one
        needs the engine to be able to take it.
        """
        bot: dict[str, Any] = self._bots.get_bot(bot_id, owner_id)
        entity_id = bot.get("entity_id")
        if not entity_id:
            raise CliToolNotFoundError(f"bot {bot_id} has no associated entity")

        engine = bot.get("active_engine") or ""
        if require_support:
            caps = capabilities_for_bot(bot, self._is_teclaw)
            if not caps.supports(ManifestCategory.CLI_TOOLS):
                raise CliToolUnsupportedError(
                    caps.reason_for(ManifestCategory.CLI_TOOLS)
                    or "this bot's engine cannot take CLI tools"
                )

        family = FAMILY_TECLAW if self._is_teclaw(engine) else FAMILY_ARCA
        ctx = CliToolContext(
            bot_id=bot_id,
            owner_id=owner_id,
            actor_id=actor_id,
            entity_id=str(entity_id),
            env=get_current_env(),
            engine_type=engine,
            tenant=get_current_avernet_tenant(),
            entity_type=bot.get("entity_type") or "staff",
        )
        return self._factory(family), ctx


__all__ = ["FAMILY_ARCA", "FAMILY_TECLAW", "BotCliToolService", "BotLookupPort"]
