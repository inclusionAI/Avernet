"""What a source delivered, and how a category reads it.

One return type for the fetch layer. ``fetch_declared`` used to answer with
``FetchedEntry | GitEntrySource`` and every consumer opened with "which one did
I get?" — eight ``isinstance`` sites across four files, all asking that question
to answer a different one: *what did this entry deliver?*

:class:`EntryDelivery` is that question's home. Two implementations satisfy it:
:class:`BlobDelivery` wraps the bytes one HTTPS GET or one object read produced,
:class:`GitDelivery` wraps a proven checkout. The caller asks for members or for
a single file and is answered on either road.

**The category keeps its authority.** ``members()`` takes ``unpack`` and
``strip_components`` as *arguments* — the materialiser reads them off the entry
and passes them down. The delivery is told what to do with what it holds; it
never reads the entry, never learns which category called, and never decides
what "the entry's bytes" are. That rule is the reason the union existed in the
first place, and it survives intact:

    what "the entry's bytes" are (a file? a package? a canonical zip?) is a
    *category* question the fetch layer must not answer.

**Reads are pure — nothing here files a receipt.** That is deliberate, and
``skills`` is why: its deliverable is a *canonical zip* that only exists after
validation, so the bytes worth a receipt are not the bytes that arrived. Filing
therefore belongs to whoever decides what the receipt stands for, which is the
caller. :meth:`EntryDelivery.receipt_url` and :meth:`EntryDelivery.auth` are
here so that caller can file under the right identity on either road.

**One discriminator survives, and it is not a type check.** ``skills`` runs two
validators by design — a fetched zip with no ``subpath`` goes byte-for-byte
through ``validate_zip``, the same validator the manual upload service runs so
that limits and layout stay one rule, while a git tree goes through
``validate_directory``. Collapsing those would discard the byte-for-byte road.
So :meth:`EntryDelivery.is_tree` asks what *arrived*, where the old code asked
what *class* it was. A third protocol that delivers a tree slots into the
existing branch instead of adding an arm to it.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitCheckout,
    git_receipt_url,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchRefusedError,
)
from agentclaw.community.core.bot_config_manifest.fetch.unpack import (
    UnpackError,
    unpack_archive,
)
from agentclaw.community.core.bot_config_manifest.schema.entries import (
    VALID_UNPACK,
)


class EntryFetchError(Exception):
    """One entry's bytes could not be acquired, with a report-safe reason.

    The reason is built from the transport's and the credential service's own
    words — W2 refuses before sending anything that would carry caller or
    source data, and W3's error family names credentials without ever carrying
    the value — so a secret cannot ride out of this module inside an
    exception. Materialisers hand ``reason`` to ``ResolveFailure`` verbatim.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


# ── the payloads a delivery wraps ────────────────────────────────────────────


@dataclass(frozen=True)
class FetchedEntry:
    """One entry's bytes, their content address, and where they came from."""

    content: bytes
    digest: str
    #: True when the platform's own copy answered — no network was touched.
    from_store: bool
    content_type: Optional[str] = None
    #: Set only when the platform's copy answered as a ``keep_last``
    #: FALLBACK — the source was fetched and failed, the stored bytes stood
    #: in. The report must say so (schema §9.6's published contract: a
    #: keep_last entry's report row states the fallback), so this carries
    #: the human-readable reason for the materialiser to surface as the
    #: entry's note. A plain store-hit (from_store, no note) is the
    #: legitimate pinned fast path and stays silent.
    fallback_reason: Optional[str] = None
    #: The URL the bytes came by, when the caller needs it for shape
    #: inference (the skills materialiser's archive-kind detection). ``None``
    #: on the roads that never knew one.
    source_url: Optional[str] = None


@dataclass(frozen=True)
class GitEntrySource:
    """A fresh git checkout for one entry to consume — files, not bytes.

    The object road hands back bytes because there was exactly one blob to
    read; the git road hands back a proven tree and lets the materialiser read
    it, because what "the entry's bytes" are is a *category* question this
    layer must not answer.

    ``file_limit`` is the category's per-entry byte cap (the same
    ``FETCH_ENTRY_LIMITS`` number the object road enforces at the transport) —
    the tree's readers refuse a member by its *declared* size against it.
    ``auth`` is the credential **name** the acquisition rode, threaded to the
    receipts so W11's lineage attributes git-sourced bytes the way it does
    object-sourced ones.
    """

    checkout: GitCheckout
    source_url: str
    subpath: Optional[str]
    moved_from: Optional[str]
    auth: Optional[str] = None
    file_limit: Optional[int] = None

    def files(self) -> list[tuple[str, bytes]]:
        try:
            return self.checkout.files(self.subpath, file_limit=self.file_limit)
        except FetchRefusedError as exc:
            raise EntryFetchError(str(exc)) from exc

    def read_file(self) -> bytes:
        try:
            return self.checkout.read_file(self.subpath, file_limit=self.file_limit)
        except FetchRefusedError as exc:
            raise EntryFetchError(str(exc)) from exc

    def receipt_url(self) -> str:
        """The W11 identity for this entry's git-sourced bytes."""
        return git_receipt_url(self.source_url, self.checkout.sha, self.subpath)

    def moved_note(self) -> Optional[str]:
        """The non-strict road's report line about a moved ref."""
        if self.moved_from is None:
            return None
        return (
            f"ref moved: the last apply recorded {self.moved_from}, "
            f"this one resolved {self.checkout.sha}"
        )


# ── the seam ─────────────────────────────────────────────────────────────────


@runtime_checkable
class EntryDelivery(Protocol):
    """What one entry's source delivered, read on the category's terms.

    Every member below has exactly one caller family, named in its docstring.
    The surface is wide because the four fetching categories genuinely want
    different things out of one delivery — but it is not *open*: a member with
    no caller does not belong here, the same discipline
    ``plugin_api/object_storage.py`` states for its own protocol.
    """

    def is_tree(self) -> bool:
        """Did the source deliver a tree, or a single object?

        ``skills`` alone asks, to pick its validator. A capability question,
        deliberately, rather than a class check: what matters is the shape
        that arrived, not which implementation produced it.
        """
        ...

    def members(
        self, *, unpack: object, strip_components: object
    ) -> list[tuple[str, bytes]] | str:
        """The delivered files as ``(relative path, bytes)``, or a refusal.

        ``resources``' directory road. A refusal comes back as its reason
        *string* rather than an exception, keeping every failure in
        ``resolve``'s currency and the bot's tree untouched.

        ``unpack`` and ``strip_components`` are the entry's declared values,
        passed in unvalidated — this method validates them (one rule, see
        :func:`archive_refusal`) because a delivery that trusted them would be
        a second, weaker gate.
        """
        ...

    def single(self) -> bytes:
        """The one file this entry delivers.

        ``resources``' file road, ``identity``, and ``cli_tools``. Raises
        :class:`EntryFetchError` when the source cannot name a single file —
        on the git road that is a checkout whose ``subpath`` names a directory
        or nothing.
        """
        ...

    def note(self) -> Optional[str]:
        """The report line this delivery owes, or ``None``.

        A ``keep_last`` fallback's reason on the object road, a moved ref's
        note on the git road. Both answer "what should the entry's report row
        say about how these bytes arrived", which is why they share a name.
        """
        ...

    def digest(self) -> Optional[str]:
        """The content address of what arrived, when one was computed.

        ``cli_tools``' declared-pin belt. ``None`` on a road that computed
        none, which the belt reads as "nothing to compare".
        """
        ...

    def receipt_url(self) -> Optional[str]:
        """The W11 identity to file this entry's bytes under."""
        ...

    def auth(self) -> Optional[str]:
        """The credential **name** the acquisition rode, for the receipt.

        Never a value. The lineage's answer to "which credential served this"
        is the same on both roads because both thread this.
        """
        ...

    def source_url(self) -> Optional[str]:
        """Where the bytes came from, for callers that infer shape from it.

        ``skills``' archive-kind detection (``.tar.gz`` vs ``.zip``).
        """
        ...

    def content_type(self) -> Optional[str]:
        """What the source said the bytes are, when it said anything.

        ``skills``' archive-kind detection again, as the second signal.
        """
        ...

    def from_store(self) -> bool:
        """True when the platform's own copy answered and no network moved.

        ``skills`` carries it onto ``_SkillPackage`` so the report can tell a
        pinned fast path from a fresh fetch.
        """
        ...


@dataclass(frozen=True)
class BlobDelivery:
    """One object's bytes, however they were acquired."""

    fetched: FetchedEntry

    def is_tree(self) -> bool:
        return False

    def members(
        self, *, unpack: object, strip_components: object
    ) -> list[tuple[str, bytes]] | str:
        """A stored git tree decoded, or an archive unpacked.

        The magic check comes **first**, and it is not an optimisation: when
        ``keep_last`` stands in for a failed *git* fetch, the store hands back
        the canonical tree as plain bytes with no idea what shape they are.
        Considering ``unpack`` before asking would send that entry down the
        archive road, where it would be told to declare an ``unpack`` a git
        source may not carry — turning a transient outage into a category
        failure instead of keeping what the bot has.
        """
        stored = decode_tree_bytes(self.fetched.content)
        if stored is not None:
            return stored
        refusal = archive_refusal(unpack, strip_components)
        if refusal is not None:
            return refusal
        assert isinstance(unpack, str)  # archive_refusal proved it
        assert isinstance(strip_components, int)
        return unpack_members(self.fetched.content, unpack, strip_components)

    def single(self) -> bytes:
        return self.fetched.content

    def note(self) -> Optional[str]:
        return self.fetched.fallback_reason

    def digest(self) -> Optional[str]:
        return self.fetched.digest

    def receipt_url(self) -> Optional[str]:
        return self.fetched.source_url

    def auth(self) -> Optional[str]:
        # The object road files its own receipt inside the fetch, credential
        # and all, so nothing downstream needs to re-file under a name.
        return None

    def source_url(self) -> Optional[str]:
        return self.fetched.source_url

    def content_type(self) -> Optional[str]:
        return self.fetched.content_type

    def from_store(self) -> bool:
        return self.fetched.from_store


@dataclass(frozen=True)
class GitDelivery:
    """A checked-out tree, read on the category's terms."""

    source: GitEntrySource

    def is_tree(self) -> bool:
        return True

    def members(
        self, *, unpack: object, strip_components: object
    ) -> list[tuple[str, bytes]] | str:
        """Every file under the composed subpath.

        ``unpack`` and ``strip_components`` are ignored, and provably safely:
        ``ARCHIVE_FIELDS_BY_KIND[SourceKind.GIT]`` is empty, so a git source
        carrying either was refused at ``PUT`` and cannot reach here. A
        repository hands over a real tree, so there is no packaging step —
        these are exactly the ``(relative path, bytes)`` pairs
        :func:`unpack_members` produces from an archive, which is what lets
        every caller downstream be one code path.
        """
        return self.source.files()

    def single(self) -> bytes:
        return self.source.read_file()

    def note(self) -> Optional[str]:
        return self.source.moved_note()

    def digest(self) -> Optional[str]:
        # Git bytes are addressed by their commit, not by a declared content
        # hash — the schema refuses ``digest`` on a git source — so there is
        # nothing here for the pin belt to compare and ``None`` says so.
        return None

    def receipt_url(self) -> Optional[str]:
        return self.source.receipt_url()

    def auth(self) -> Optional[str]:
        return self.source.auth

    def source_url(self) -> Optional[str]:
        return self.source.source_url

    def content_type(self) -> Optional[str]:
        return None

    def from_store(self) -> bool:
        return False


# ── the shared mechanics ─────────────────────────────────────────────────────


def archive_refusal(unpack: object, strip_components: object) -> Optional[str]:
    """The archive fields a directory entry over ``oss`` must carry, or why not.

    Pure, and asked twice on purpose: once by ``resources`` **before** the
    fetch, so a doomed entry costs no network against this apply's byte budget
    and lock TTL, and once by :meth:`BlobDelivery.members` after, for the entry
    whose protocol that belt could not determine up front. Two calls of one
    function, never two rules — which is why it takes the two *values* rather
    than the entry: the pre-fetch caller has an entry, the delivery does not.
    """
    # Str first: ``VALID_UNPACK`` is a frozenset, and an unhashable ``unpack``
    # (a YAML list) would raise on the membership test where this owes a
    # clean refusal.
    if not isinstance(unpack, str) or unpack not in VALID_UNPACK:
        return (
            "a directory entry fetched over 'oss' must declare "
            "'unpack: zip|tar.gz' — one request fetches one object, so the "
            "tree travels as an archive"
        )
    if (
        not isinstance(strip_components, int)
        or isinstance(strip_components, bool)
        or strip_components < 0
    ):
        return "'strip_components' must be a non-negative integer"
    return None


def unpack_members(
    archive: bytes, kind: str, strip_components: int
) -> list[tuple[str, bytes]] | str:
    """The guarded unpack, platform-side, into a throwaway directory.

    The bot is never a scratch space: ``unpack_archive`` writes only into a
    fresh temporary directory, so a bad or oversized archive (W1's member /
    unpacked-size limits live inside it) fails before anything is delivered.
    Returned members are ``(relative path, bytes)`` with ``strip_components``
    already applied — the bytes are read back before the throwaway dir goes
    away; a refusal comes back as its reason string rather than an exception,
    keeping every failure in ``resolve``'s currency and the bot's tree
    untouched.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="manifest-resources-") as tmp:
            tree = unpack_archive(
                archive,
                kind,
                Path(tmp) / "tree",
                strip_components=strip_components,
            )
            # ``UnpackedTree.members`` are the tree's files only — directories
            # are structural — relative to ``root``.
            return [(name, (tree.root / name).read_bytes()) for name in tree.members]
    except UnpackError as exc:
        return str(exc)


#: Marks the stored form of a git-delivered tree. Self-describing on purpose:
#: ``keep_last`` reads a receipt back as plain bytes with no idea what shape
#: they are, and "guess from the URL" is not identification. The version digit
#: is what lets the framing change later without a stored copy being decoded
#: under the wrong rules.
TREE_MAGIC = b"acm-tree-v1\n"


def canonical_tree_bytes(members: list[tuple[str, bytes]]) -> bytes:
    """One deterministic byte string standing for a whole delivered tree.

    Two jobs, and the second is why it is **reversible**:

    1. A stable content address for the store — the same tree must hash the
       same on every apply, and two different trees must not collide. Members
       are sorted by path and each is framed with its path, its length and its
       bytes: length-prefixed rather than delimiter-joined, because a delimiter
       is something a *path or a payload* could contain, and a tree that could
       be made to hash as another tree is a receipt that proves nothing.
    2. The bytes ``keep_last`` stands in with when a later git fetch fails.
       ``on_fetch_failure: keep_last`` is the default and it promises the bot
       keeps running what it has — a promise an unreadable receipt cannot keep.

    Never delivered to a bot as-is: :func:`decode_tree_bytes` turns it back
    into the members, and those are the intents.
    """
    parts: list[bytes] = [TREE_MAGIC]
    for rel, data in sorted(members):
        encoded = rel.encode("utf-8")
        parts.append(b"%d:%s%d:" % (len(encoded), encoded, len(data)))
        parts.append(data)
    return b"".join(parts)


def decode_tree_bytes(blob: bytes) -> Optional[list[tuple[str, bytes]]]:
    """The members back out, or ``None`` when these are not a canonical tree.

    ``None`` rather than an exception: the caller is asking "is this a stored
    git tree or an archive?", and a shape it does not recognise is an answer,
    not a fault. Truncation and a bad length are the same answer — a receipt
    that does not decode cleanly must not half-deliver a tree.
    """
    if not blob.startswith(TREE_MAGIC):
        return None
    members: list[tuple[str, bytes]] = []
    at = len(TREE_MAGIC)
    try:
        while at < len(blob):
            colon = blob.index(b":", at)
            name_len = int(blob[at:colon])
            at = colon + 1
            name = blob[at : at + name_len].decode("utf-8")
            at += name_len
            colon = blob.index(b":", at)
            size = int(blob[at:colon])
            at = colon + 1
            if at + size > len(blob):
                return None
            members.append((name, blob[at : at + size]))
            at += size
    except (ValueError, UnicodeDecodeError):
        return None
    return members


__all__ = [
    "BlobDelivery",
    "EntryDelivery",
    "EntryFetchError",
    "FetchedEntry",
    "GitDelivery",
    "GitEntrySource",
    "TREE_MAGIC",
    "archive_refusal",
    "canonical_tree_bytes",
    "decode_tree_bytes",
    "unpack_members",
]
