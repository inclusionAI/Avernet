"""A bot-to-bot workspace file sync, engine-dispatched behind one port.

An outbound port: the ``BOT_FILE_SYNC`` materialiser hands the destination bot
and the declaration (which source bot, literal path, excludes) to this port,
and the port syncs the source bot's workspace onto the destination's.

**The port owns engine-specific storage.** "workspace" is the abstraction a
copy manifest carries (a literal ``path`` the serializer writes); how the sync
is physically performed differs per engine and is the implementation's concern,
not the materialiser's. A live ``openclaw`` bot syncs by NAS rsync; an aicoding
bot syncs through fastdisk + OSS; the port picks the strategy and builds the
real source/target paths from the two bot records. The materialiser never
sees a host path, never touches the content store, and never reads bytes
through the fetch chain — that is what makes this construct a sync rather than
a stored-content resource.

Members are ``@abstractmethod``, and every implementation inherits this port
rather than satisfying it structurally: the backend runs no static type
checker, so a structurally-satisfied Protocol is verified by nothing, and
abstract members make a dropped or renamed method fail at construction instead
of resolving to a ``...`` stub that synchronises nothing.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class BotFileSyncTarget:
    """The destination bot's identifying coords, lifted off ``ApplyContext``
    by the materialiser.

    The port stays import-light: it never names ``apply_engine.context``
    (that would invert the ports → apply_engine consumer edge into a cycle),
    so the materialiser, which lives in apply_engine and owns the context,
    lifts the coords the implementation resolves devices and engine strategy
    against, and hands them over as this value. ``entity_type`` is one of them:
    a NAS-path resolver keys the storage id on it (``{env}_{entity_type}_…``),
    and the port reads it off the dst bot record at the on_container phase
    (the record exists by then). A missing record falls back to ``"staff"``,
    the same default the rest of the codebase reads a bot record's
    ``entity_type`` with.
    """

    bot_id: str
    owner_id: str
    entity_id: str
    env: str
    engine_type: str
    entity_type: str


@dataclass(frozen=True)
class BotFileSyncResult:
    """What a sync answered: whether the workspace converged, and why not.

    ``reason`` carries an engine-appropriate message on failure, for the apply
    report — never raw exception text, the same discipline the resources
    materialiser holds. ``ok`` is the one bit the materialiser turns into an
    entry outcome.
    """

    ok: bool
    reason: str | None = None


@runtime_checkable
class BotFileSyncPort(Protocol):
    """Sync one source bot's workspace onto the destination bot named by ``ctx``.

    ``path`` is the literal the copy serializer wrote (``workspace/``); the
    implementation resolves it to real source/target host paths under the two
    bots' engine roots. ``excludes`` is the literal-relative-path list the
    manifest carried (``normalize_build_ignore_path`` governance); the
    implementation turns it into its own protocol's filter form (rsync
    ``--exclude`` / a fastdisk request field / …).

    Raises nothing the apply report would carry raw: an unreachable source
    bot, a missing device binding, a sync command that failed — each is
    returned as ``BotFileSyncResult(ok=False, reason=…)``, so the caller
    translates it to an entry failure the way it does any resolve/write fault.
    """

    @abstractmethod
    async def sync(
        self,
        *,
        target: BotFileSyncTarget,
        source_bot_id: str,
        source_owner_id: str,
        path: str,
        excludes: tuple[str, ...],
    ) -> BotFileSyncResult:
        """Converge the source bot's workspace tree onto the destination's.

        ``source_owner_id`` disambiguates the source row when a ``source_bot_id``
        is not globally unique — the reserved ``default`` bot has a row per
        account, so the sync reads ``(source_bot_id, source_owner_id)`` rather
        than by ``source_bot_id`` alone.
        """
        ...


__all__ = ["BotFileSyncPort", "BotFileSyncResult", "BotFileSyncTarget"]
