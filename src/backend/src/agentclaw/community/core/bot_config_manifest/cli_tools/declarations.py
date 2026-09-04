"""What a caller declares, and what one operation reports back (W9, #1477).

Both callers speak this vocabulary: the HTTP route builds a
:class:`CliToolDecl` from its request body, the ``cli_tools`` materialiser
builds one from a manifest entry, and neither carries a shape the other does
not. The report side is :class:`CliToolOutcome`, one per tool, because a full
override can succeed for three tools and fail for the fourth and the caller
needs to be told which.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Optional

from agentclaw.community.core.bot_config_manifest.cli_tools.models import (
    BotCliToolRecord,
)


class CliToolStatus(StrEnum):
    """What happened to one tool — an outcome, not an instruction.

    Named for what it reports rather than for what was attempted: every member
    is a result the caller reads back off a :class:`CliToolOutcome`, and
    nothing chooses an operation from it.

    Deliberately not ``apply/outcomes.EntryOutcome``: that vocabulary has no
    ``REMOVED``, because in every other category a removal is not a declared
    entry. Here it is the visible half of a full override — "the declaration
    stopped naming it, so it is gone" is exactly what the caller must be able
    to read — and the materialiser maps these onto the report's own.
    """

    INSTALLED = "installed"
    UNCHANGED = "unchanged"
    REMOVED = "removed"
    FAILED = "failed"
    #: The bot already had a tool by that name, and the caller asked for an
    #: install rather than a replacement. Distinct from ``FAILED`` because it is
    #: the API's 409 rather than its 422: nothing was wrong with the
    #: declaration, the name was simply taken.
    CONFLICT = "conflict"


@dataclass(frozen=True)
class CliToolDecl:
    """One declared tool: a command name and where its bytes come from.

    **One entry is one command is one file** (schema §3.7). ``subpath`` picks
    that file out of an archive; without ``unpack`` the fetched object *is*
    the file. ``digest`` is mandatory — the schema refuses a ``cli_tools``
    entry without one, and this type does not make it optional either, because
    the whole category rests on the platform never distributing an unpinned
    executable.
    """

    name: str
    source_url: str
    digest: str
    subpath: Optional[str] = None
    #: ``"zip"`` / ``"tar.gz"``, or ``None`` when the source is the file itself.
    unpack: Optional[str] = None
    #: Metadata only. It never affects convergence — two installs of the same
    #: bytes under different ``version`` strings are the same tool.
    version: Optional[str] = None
    #: The credential *name* the fetch rides, never a secret.
    auth: Optional[str] = None
    #: ``on_fetch_failure`` — the stored copy may stand in for an unreachable
    #: source, per the schema's default.
    keep_last: bool = True

    @classmethod
    def from_entry(cls, entry: Mapping[str, Any]) -> CliToolDecl:
        """A validated manifest entry, as this vocabulary.

        Reads only keys ``CATEGORY_ENTRY_KEYS[CLI_TOOLS]`` allows; the entry
        has already been through the schema, so the shapes are trusted and the
        conversion asserts nothing a second time. Note the absence of
        ``strip_components``: unlike ``resources``, a ``cli_tools`` entry may
        not declare one, so ``subpath`` names a member of the archive exactly
        as it is packed.
        """
        return cls(
            name=entry["name"],
            source_url=entry.get("from") or entry.get("source") or "",
            digest=entry.get("digest") or "",
            subpath=entry.get("subpath"),
            unpack=entry.get("unpack"),
            version=entry.get("version"),
            auth=entry.get("auth"),
            keep_last=entry.get("on_fetch_failure", "keep_last") == "keep_last",
        )

    @property
    def convergence_key(self) -> tuple[str, Optional[str]]:
        """What decides whether an installed tool already satisfies this.

        ``(digest, subpath)`` and never the digest alone: one archive can carry
        two commands, so two declarations sharing a digest are the same tool
        only if they also select the same member. ``version`` is excluded on
        purpose — it is a label, and letting it force a reinstall would make
        an edit to a comment redeliver a 200 MiB binary.
        """
        return (self.digest, self.subpath)


@dataclass(frozen=True)
class CliToolOutcome:
    """What happened to one tool, and why if it failed."""

    name: str
    status: CliToolStatus
    detail: Optional[str] = None
    record: Optional[BotCliToolRecord] = None

    @property
    def failed(self) -> bool:
        return self.status in (CliToolStatus.FAILED, CliToolStatus.CONFLICT)


@dataclass(frozen=True)
class CliToolDrift:
    """The platform's table against what the family says the bot has.

    ``observable`` is ``False`` on a family that cannot be asked — teclaw,
    whose artifact is composed *from* the table and therefore cannot disagree
    with it independently. Reporting that as "no drift" would be a claim the
    platform is not entitled to make.
    """

    recorded: tuple[str, ...]
    reported: tuple[str, ...] = ()
    #: Recorded by the platform, not reported by the bot.
    missing_on_bot: tuple[str, ...] = ()
    #: Reported by the bot, not recorded by the platform.
    unrecorded: tuple[str, ...] = ()
    observable: bool = True
    reason: Optional[str] = None

    @property
    def converged(self) -> bool:
        return self.observable and not self.missing_on_bot and not self.unrecorded


__all__ = [
    "CliToolDecl",
    "CliToolDrift",
    "CliToolStatus",
    "CliToolOutcome",
]
