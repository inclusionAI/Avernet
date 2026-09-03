"""``CliToolService`` — the one component that installs a bot's CLI tools.

Two callers, one implementation. The HTTP routes under
``/openapi/v1/bots/{bot_id}/cli-tools`` and the manifest's ``cli_tools``
materialiser both call *this*; neither reimplements a step, and backend code
never calls the platform's own HTTP endpoints to reach it (spec D-2). That is
the whole reason this class exists as a service rather than as logic inside a
route: two implementations of "install a tool" would diverge on exactly the
checks that matter — the digest, the architecture, whether a failed placement
still writes a row.

**The order is the design.**

    fetch → digest → unpack → select subpath → verify ELF → md5
          → store in OSS → deliver → record

The OSS write comes *before* delivery because on teclaw it **is** the delivery:
the composed artifact references the stored object (spec D-4). On ARCA it is
what makes a later redelivery possible without re-fetching a source URL that
may have rotated. The row is written *last* because the table is the platform's
claim that the bot has the tool, and a claim made before the engine accepted it
is a claim that can be false. Nothing is recorded for a step that failed.

**Full override is the manifest's shape, and removals come from the table.**
``replace_all`` computes what to remove from ``ac_bot_cli_tool``, never from
the engine's listing: a tool the platform installed must be removed even when
the engine's view has drifted, and a listing that came back short would
otherwise silently leave it behind. It returns one outcome per tool, so a
partial failure is *reported* rather than left for the caller to reconcile.

**Nothing here branches on engine type and nothing composes a path.** The
family difference is which :class:`CliToolDeliveryPort` the strategy bound, and
the directory a tool lands in is the engine's answer, asked inside its own
``install``.
"""
from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import Callable, Optional, Sequence

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetchError,
    EntryFetcher,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.context import (
    CliToolContext,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.declarations import (
    CliToolDecl,
    CliToolDrift,
    CliToolOp,
    CliToolOutcome,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.delivery_port import (
    CliToolDeliveryError,
    CliToolDeliveryPort,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.models import (
    BotCliToolRecord,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.store import (
    CliToolStore,
    CliToolStoreError,
    checked_name,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.verify import (
    CliToolSubpathError,
    CliToolVerificationError,
    select_subpath,
    verify_amd64_elf,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FetchCategory,
)
from agentclaw.community.core.bot_config_manifest.fetch.unpack import (
    UnpackError,
    unpack_archive,
)
from agentclaw.community.core.repository.protocols.bot.cli_tool import (
    BotCliToolRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

#: The fetch category, and with it the 200 MiB per-entry width schema §5 gives
#: this category. Named rather than inlined so the width is read from one table.
FETCH_CATEGORY = FetchCategory.CLI_TOOLS.value


class CliToolService:
    """Install, remove, list and replace a bot's CLI tools."""

    def __init__(
        self,
        *,
        repo: BotCliToolRepositoryProtocol,
        store: CliToolStore,
        delivery: CliToolDeliveryPort,
        entry_fetcher: EntryFetcher,
    ) -> None:
        self._repo = repo
        self._store = store
        self._delivery = delivery
        self._fetcher = entry_fetcher

    # ── reads ────────────────────────────────────────────────────────────

    def list(self, ctx: CliToolContext) -> Sequence[BotCliToolRecord]:
        """The platform's record — the answer to "what does this bot have"."""
        return self._repo.list(
            env=ctx.env, entity_id=ctx.entity_id, bot_id=ctx.bot_id
        )

    def get(self, ctx: CliToolContext, name: str) -> Optional[BotCliToolRecord]:
        return self._repo.get(
            env=ctx.env, entity_id=ctx.entity_id, bot_id=ctx.bot_id, name=name
        )

    async def drift(self, ctx: CliToolContext) -> CliToolDrift:
        """The table against the family's listing — observable, not assumed.

        A family that cannot be asked reports ``observable=False`` with its
        reason rather than an empty diff, because "no drift" and "I did not
        look" are different answers and only one of them is a fact.
        """
        recorded = tuple(record.name for record in self.list(ctx))
        try:
            reported = tuple(await self._delivery.list(ctx))
        except CliToolDeliveryError as error:
            return CliToolDrift(
                recorded=recorded, observable=False, reason=str(error)
            )
        return CliToolDrift(
            recorded=recorded,
            reported=reported,
            missing_on_bot=tuple(n for n in recorded if n not in set(reported)),
            unrecorded=tuple(n for n in reported if n not in set(recorded)),
        )

    # ── writes ───────────────────────────────────────────────────────────

    async def install(
        self,
        ctx: CliToolContext,
        decl: CliToolDecl,
        *,
        installed_by: str,
        expect_absent: bool = False,
    ) -> CliToolOutcome:
        """Fetch, verify, store, deliver and record one tool.

        Every failure comes back as a ``FAILED`` outcome carrying the reason,
        rather than as an exception: a full override installing four tools must
        report the one that failed alongside the three that did not, and a
        caller that wanted an exception can read ``outcome.failed``.

        ``expect_absent`` is the management API's contract, and the *only* thing
        that makes its 409 true: the row is then written with an insert whose
        UNIQUE constraint decides, rather than an upsert that would quietly turn
        a losing concurrent install into a replacement. A manifest apply leaves
        it off — a full override is entitled to replace.
        """
        try:
            checked_name(decl.name)
        except ValueError as error:
            return CliToolOutcome(decl.name, CliToolOp.FAILED, str(error))

        try:
            data = await self._acquire(ctx, decl)
        except (
            EntryFetchError, UnpackError, CliToolSubpathError,
            CliToolVerificationError, ValueError,
        ) as error:
            return CliToolOutcome(decl.name, CliToolOp.FAILED, str(error))

        md5 = hashlib.md5(data).hexdigest()
        # Read before the write: a replacement must know which object the
        # surviving row points at, so a failed delivery can discard only what
        # nothing references.
        superseded = self.get(ctx, decl.name)
        try:
            stored = await asyncio.to_thread(
                self._store.put,
                ctx.scope,
                name=decl.name,
                digest=decl.digest,
                data=data,
            )
        except CliToolStoreError as error:
            return CliToolOutcome(decl.name, CliToolOp.FAILED, str(error))

        try:
            await self._delivery.install(ctx, name=decl.name, data=data)
        except CliToolDeliveryError as error:
            # The engine refused. The row is the platform's claim that the bot
            # has the tool, so it is not written — and the object just stored
            # is removed, because nothing will reference it and nothing else
            # would ever collect it (its key is derived, not recorded).
            #
            # Safe on a replacement too, and only because the live key carries a
            # content fingerprint: the bytes just written are at a key of their
            # own, so discarding them cannot touch the object the surviving row
            # still describes. The one case it must not fire is a re-install of
            # the *same* digest, where both rows name the same key.
            if superseded is None or superseded.oss_key != stored.store_key:
                await self._discard(stored.store_key)
            return CliToolOutcome(decl.name, CliToolOp.FAILED, str(error))

        write = self._repo.insert if expect_absent else self._repo.upsert
        record = await asyncio.to_thread(
            write,
            env=ctx.env,
            entity_id=ctx.entity_id,
            bot_id=ctx.bot_id,
            name=decl.name,
            source=decl.source_url,
            digest=decl.digest,
            subpath=decl.subpath,
            md5=md5,
            size_bytes=len(data),
            version=decl.version,
            oss_key=stored.store_key,
            installed_by=installed_by,
            modifier=ctx.actor_id,
        )
        if record is None:
            # ``insert`` alone can answer this, and only at the moment of the
            # write: the name was free when this install started and is taken
            # now. The object just stored is unreferenced and nothing else would
            # ever collect it, so it is discarded — but only after checking it
            # is not the *winner's*, which it is when both racers installed the
            # same bytes and the content-addressed key came out identical.
            winner = self.get(ctx, decl.name)
            if winner is None or winner.oss_key != stored.store_key:
                await self._discard(stored.store_key)
            return CliToolOutcome(
                decl.name,
                CliToolOp.CONFLICT,
                f"the bot already has a CLI tool named {decl.name!r}",
            )
        if superseded is not None and superseded.oss_key != stored.store_key:
            # The replaced version's object is unreferenced the moment the row
            # is replaced, and its key is held nowhere else. Collected here or
            # never. Reached whenever the digest changed — the ordinary
            # replacement — and also when the row predates a store-base change.
            await self._discard(superseded.oss_key)
        logger.info(
            "[cli_tools] installed bot=%s name=%s size=%d by=%s",
            ctx.bot_id, decl.name, len(data), installed_by,
        )
        return CliToolOutcome(decl.name, CliToolOp.INSTALLED, record=record)

    async def remove(self, ctx: CliToolContext, name: str) -> CliToolOutcome:
        """Delete the tool, the row and the object. In that order.

        The row is read first because it holds the object key: a delete that
        dropped the row and then asked where the bytes were could never find
        them again.
        """
        record = self.get(ctx, name)
        if record is None:
            return CliToolOutcome(
                name, CliToolOp.FAILED, f"the bot has no CLI tool named {name!r}"
            )
        return await self._remove_record(ctx, record)

    async def _remove_record(
        self, ctx: CliToolContext, record: BotCliToolRecord
    ) -> CliToolOutcome:
        """The removal itself, for a caller that already holds the row.

        ``replace_all`` has just listed the table, so re-reading each row it is
        about to remove would be one query per removal for an answer it has.
        """
        name = record.name
        try:
            await self._delivery.delete(ctx, name=name)
        except CliToolDeliveryError as error:
            return CliToolOutcome(name, CliToolOp.FAILED, str(error))
        await asyncio.to_thread(
            self._repo.delete,
            env=ctx.env, entity_id=ctx.entity_id, bot_id=ctx.bot_id, name=name,
        )
        await self._discard(record.oss_key)
        logger.info("[cli_tools] removed bot=%s name=%s", ctx.bot_id, name)
        return CliToolOutcome(name, CliToolOp.REMOVED, record=record)

    async def replace_all(
        self, ctx: CliToolContext, decls: Sequence[CliToolDecl], *, installed_by: str
    ) -> list[CliToolOutcome]:
        """Full override: the declared set becomes the installed set.

        What the manifest calls. Removals are computed **from the table**, so a
        tool the platform installed is removed even when the engine's view has
        drifted; a declaration whose ``(digest, subpath)`` already matches its
        row plans ``unchanged`` and is not re-fetched, which is what keeps a
        no-op apply from redelivering every binary the bot has.
        """
        existing = {record.name: record for record in self.list(ctx)}
        declared = {decl.name for decl in decls}
        outcomes: list[CliToolOutcome] = []

        for name in sorted(set(existing) - declared):
            outcomes.append(await self._remove_record(ctx, existing[name]))

        for decl in decls:
            current = existing.get(decl.name)
            if current is not None and current.convergence_key == decl.convergence_key:
                outcomes.append(
                    CliToolOutcome(decl.name, CliToolOp.UNCHANGED, record=current)
                )
                continue
            outcomes.append(await self.install(ctx, decl, installed_by=installed_by))
        return outcomes

    async def remove_all(self, ctx: CliToolContext) -> int:
        """Drop every tool the bot has — the creation-cleanup entry point.

        A W13 creation that fails after installing tools would otherwise leave
        rows for a bot that was never created, and the objects behind them.
        The rows' keys are collected by the delete itself, because ``oss_key``
        lives only on the rows and a caller that deleted first could never
        enumerate what it had just orphaned.
        """
        keys = await asyncio.to_thread(
            self._repo.delete_all,
            env=ctx.env, entity_id=ctx.entity_id, bot_id=ctx.bot_id,
        )
        for key in keys:
            await self._discard(key)
        return len(keys)

    # ── the pipeline ─────────────────────────────────────────────────────

    async def _acquire(self, ctx: CliToolContext, decl: CliToolDecl) -> bytes:
        """Fetch, confirm the pin, unpack if declared, select and verify."""
        fetched = await asyncio.to_thread(
            self._fetcher.fetch,
            ctx,
            source_url=decl.source_url,
            digest=decl.digest,
            auth=decl.auth,
            category=FETCH_CATEGORY,
            keep_last=decl.keep_last,
            entry_identity=decl.name,
        )
        if decl.digest and fetched.digest != decl.digest:
            # The fetch pipeline enforces the pin; this compares the content
            # address it already computed, so the belt costs nothing. It is
            # here because this is the one category that distributes an
            # executable, and a keep_last fallback is the path where stored
            # bytes could stand in for what was declared.
            raise ValueError(
                f"{decl.name!r}: the bytes are {fetched.digest}, the entry "
                f"declared {decl.digest}"
            )
        if not decl.unpack:
            if decl.subpath:
                raise CliToolSubpathError(
                    f"{decl.name!r}: 'subpath' selects a member of an archive, "
                    "but the entry declares no 'unpack' — without it the fetched "
                    "object is the command itself and the selection would be "
                    "silently ignored"
                )
            data = fetched.content
        else:
            data = await asyncio.to_thread(
                self._select, fetched.content, decl,
            )
        verify_amd64_elf(data, name=decl.name)
        return data

    @staticmethod
    def _select(archive: bytes, decl: CliToolDecl) -> bytes:
        """The declared member of an archive, unpacked into a throwaway dir.

        The bot is never a scratch space and neither is the platform: the tree
        lives for the length of this call, the one selected file is read back,
        and everything else is discarded. ``strip_components`` is deliberately
        absent — a ``cli_tools`` entry may not declare one, so ``subpath``
        names a member exactly as the archive packs it.
        """
        if not decl.subpath:
            raise CliToolSubpathError(
                f"{decl.name!r}: 'unpack' is declared, so 'subpath' must name "
                "the one file in the archive that is the command"
            )
        with tempfile.TemporaryDirectory(prefix="manifest-cli-tools-") as tmp:
            tree = unpack_archive(archive, decl.unpack, Path(tmp) / "tree")
            member = select_subpath(tree, decl.subpath, location=decl.name)
            return member.read_bytes()

    async def _discard(self, key: str) -> None:
        """Remove an object whose row is gone or was never written.

        A failure here is logged, not raised: the caller's operation already
        succeeded (or already failed for its own reason), and turning a
        leftover object into a second failure would misreport what happened.
        The object is still there for a later purge to find.
        """
        try:
            await asyncio.to_thread(self._store.delete, key=key)
        except CliToolStoreError as error:
            logger.warning(
                "[cli_tools] object left behind at %s: %s", key, error
            )


class CliToolPurger:
    """Drop a bot's tool rows and the objects behind them, with no engine call.

    The creation-cleanup entry point, and deliberately **not**
    :meth:`CliToolService.remove_all`: it runs when a W13 creation ended
    *without a bot*, so there is no container to remove a tool from and asking
    an engine would be asking about something that never existed. It is also
    synchronous, because the discard path that calls it is.

    The rows' ``oss_key``s are collected by the delete itself: that column lives
    only on those rows, so a caller that deleted first could never enumerate
    what it had just orphaned.
    """

    def __init__(
        self, *, repo: BotCliToolRepositoryProtocol, store: CliToolStore
    ) -> None:
        self._repo = repo
        self._store = store

    def __call__(self, entity_id: str, bot_id: str) -> int:
        """Objects removed. Never raises — the caller is already ending."""
        keys = self._repo.delete_all(
            env=get_current_env(), entity_id=entity_id, bot_id=bot_id
        )
        return self._store.purge(keys)


#: How the DI graph hands this service around: a factory keyed by engine
#: family, because the family is the only thing that differs — the table, the
#: object store and the fetch funnel are shared, and the family decides which
#: delivery port sits inside. Named rather than spelled structurally at each
#: binding site so the injector's key is one object, not two equal ones.
CliToolServiceFactory = Callable[[str], CliToolService]


__all__ = [
    "FETCH_CATEGORY",
    "CliToolPurger",
    "CliToolService",
    "CliToolServiceFactory",
]
