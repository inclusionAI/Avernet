"""What a caller declares, and what one operation reports back (W9, #1477).

Both callers speak this vocabulary: the HTTP route builds a
:class:`CliToolDecl` from its upload's form fields, the ``cli_tools``
materialiser builds one from a manifest entry, and neither carries a shape the
other does not. What differs is only *where the bytes come from* — an upload
carries them, a manifest entry names a source to resolve — and that is the one
thing the declaration deliberately does not model as a field. The report side
is :class:`CliToolOutcome`, one per tool, because a full override can succeed
for three tools and fail for the fourth and the caller needs to be told which.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    """One declared tool: a command name and what to make of its bytes.

    **One entry is one command is one file** (schema §3.7). ``subpath`` picks
    that file out of an archive; without ``unpack`` the acquired object *is*
    the file. ``digest`` is mandatory — the schema refuses a ``cli_tools``
    entry without one, and this type does not make it optional either, because
    the whole category rests on the platform never distributing an unpinned
    executable. (The one exemption is the git road, whose commit SHA is the
    pin; there the field is empty.)

    **Where the bytes come from is not one of its fields.** There are two
    roads and each carries its own payload: a manifest entry goes through
    ``fetch_declared`` — see :attr:`entry` — and an upload arrives as bytes the
    caller already holds. Neither is a URL this type could hold, and holding
    one is precisely how a source *name* once went on the wire as though it
    were an address.
    """

    name: str
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
    #: The manifest entry this was read from — **the manifest road's payload**.
    #:
    #: Present ⇒ the acquisition goes through ``fetch_declared``, which is the
    #: only thing that can resolve a ``from`` name or a ``protocol: git``
    #: source. ``CliToolService.install`` requires it, because that method *is*
    #: the manifest road; the upload road has its own entry point and carries
    #: bytes instead, so a decl built for it leaves this ``None`` and never
    #: reaches a fetch.
    #:
    #: ``compare=False`` on purpose. This field is *how* the bytes are
    #: acquired, not *what* is declared: two entries naming the same tool from
    #: the same place are the same declaration whichever mapping they were read
    #: from. Comparing it would also make the type unhashable (a dict), and
    #: would let a whitespace-level edit to a manifest read as a changed tool.
    entry: Optional[Mapping[str, Any]] = field(default=None, compare=False)

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
            entry=dict(entry),
            digest=entry.get("digest") or "",
            subpath=entry.get("subpath"),
            unpack=entry.get("unpack"),
            version=entry.get("version"),
            auth=entry.get("auth"),
            keep_last=entry.get("on_fetch_failure", "keep_last") == "keep_last",
        )

    @property
    def declared_address(self) -> str:
        """How this entry named its source, for the row and the report to echo.

        **Read by nobody that fetches.** The acquisition goes through
        :attr:`entry`; this is the audit column's value, and it is deliberately
        whatever the document *said* — a ``from`` name, a git URL, an empty
        string on the upload road, where the caller declared no source at all.
        Reading a value like this as though it were a fetchable address is the
        defect this type was restructured to make unspellable.
        """
        return _declared_address(self.entry) if self.entry is not None else ""

    @property
    def convergence_key(self) -> Optional[tuple[str, Optional[str]]]:
        """What decides whether an installed tool already satisfies this.

        ``(digest, subpath)`` and never the digest alone: one archive can carry
        two commands, so two declarations sharing a digest are the same tool
        only if they also select the same member. ``version`` is excluded on
        purpose — it is a label, and letting it force a reinstall would make
        an edit to a comment redeliver a 200 MiB binary.

        **``None`` when there is no digest**, which is the git road: a git
        source declares no digest (the commit SHA is the pin) and the SHA is
        not known until the ref is resolved, which happens after planning. A
        key of ``("", subpath)`` would make every git-sourced tool at one path
        compare equal to every other — so a moved ref would plan ``unchanged``
        and survive, which is the one outcome convergence exists to prevent.
        ``None`` means "never equal": the tool is re-acquired every apply, the
        same conservative answer ``resources`` gives for the same reason.
        """
        if not self.digest:
            return None
        return (self.digest, self.subpath)


def _declared_address(entry: Mapping[str, Any]) -> str:
    """How the entry named its source, for the report to echo.

    A ``from`` is the source's name, which is what the apply report's ``from``
    column carries for every other category too; an inline declaration is its
    ``url`` (git) or its ``bucket/key`` (oss).
    """
    if isinstance(entry.get("from"), str):
        return entry["from"]
    inline = entry.get("source")
    if not isinstance(inline, Mapping):
        return ""
    url = inline.get("url")
    if url:
        return str(url)
    bucket, key = inline.get("bucket"), inline.get("key")
    return f"{bucket}/{key}" if bucket else str(key or "")


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
