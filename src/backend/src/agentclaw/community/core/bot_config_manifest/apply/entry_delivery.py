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

Grammar reference: ``docs/bot-config-manifest/manifest-schema.zh-CN.md``
— §3.2 (``resources``), §3.3 (``skills``). Cite a section rather than restating the grammar here;
two copies of one grammar drift, and the document is the one users read.
"""
from __future__ import annotations

import tempfile
from abc import abstractmethod
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
    """One entry's bytes, their content address, and where they came from.

    Three states, and a caller tells them apart by ``from_store`` and
    ``fallback_reason``.

    A **fresh fetch** — the wire moved and the bytes were filed with the
    store::

        FetchedEntry(
            content=b"PK\x03\x04...",
            digest="sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b...",
            from_store=False,
            content_type="application/zip",
            fallback_reason=None,
            source_url="https://example.com/tools/qc-v2.zip",
        )

    A **pinned store hit** — the entry declared a ``digest``, the platform
    already held those exact bytes, and no network was touched. Silent by
    design: this is the legitimate fast path, so there is no note::

        FetchedEntry(
            content=b"PK\x03\x04...",
            digest="sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b...",
            from_store=True,
            content_type="application/zip",
            fallback_reason=None,
            source_url="oss://team-artifacts/tools/qc/v2.tgz",
        )

    A **keep_last fallback** — the source was fetched, it failed, and the
    stored copy stood in. ``from_store`` is true *and* a reason is set, which
    is the only combination that means "fallback"::

        FetchedEntry(
            content=b"acm-tree-v1\n...",
            digest="sha256:2c26b46b68ffc68ff99b453c1d30413413422d70...",
            from_store=True,
            content_type=None,
            fallback_reason=(
                "delivered from the platform's stored copy (keep_last): "
                "the git fetch failed"
            ),
            source_url=(
                "git+https://code.example.com/team/content.git"
                "@4f2a9c1b8e7d6a5c4b3a2918f7e6d5c4b3a29187:kb"
            ),
        )

    Created by: ``apply/entry_fetch.EntryFetcher`` — ``fetch``,
    ``acquire_object`` and ``_git_keep_last``.
    Consumed by: ``apply/entry_delivery.BlobDelivery``, which is the only thing
    that wraps one.
    """

    #: The entry's bytes, whole and in memory. For a stored git tree these are
    #: the canonical framing :func:`canonical_tree_bytes` produced, not a file.
    content: bytes
    #: ``"sha256:"`` followed by 64 lowercase hex characters, over ``content``.
    digest: str
    #: True when the platform's own copy answered — no network was touched.
    #: True for both a pinned hit and a ``keep_last`` fallback;
    #: ``fallback_reason`` is what separates them.
    from_store: bool
    #: What the source said the bytes are, e.g. ``"application/zip"``, or
    #: ``None`` when it said nothing. The object-store road never sets one.
    content_type: Optional[str] = None
    #: Set only when the platform's copy answered as a ``keep_last``
    #: FALLBACK — the source was fetched and failed, the stored bytes stood
    #: in. The report must say so (schema §9.6's published contract: a
    #: keep_last entry's report row states the fallback), so this carries
    #: the human-readable reason for the materialiser to surface as the
    #: entry's note. A plain store-hit (from_store, no note) is the
    #: legitimate pinned fast path and stays silent.
    fallback_reason: Optional[str] = None
    #: The address these bytes are filed under, in one of three shapes
    #: depending on the road that produced them::
    #:
    #:     "https://example.com/tools/qc-v2.zip"       # the API install road
    #:     "oss://team-artifacts/tools/qc/v2.tgz"      # object store road
    #:     "git+https://code.example.com/team/content.git@<40-hex sha>:kb"
    #:
    #: The last two are receipt identities, not fetchable URLs. Callers also
    #: read this to infer an archive's kind from its extension (the skills
    #: materialiser). ``None`` on the roads that never knew one.
    source_url: Optional[str] = None


@dataclass(frozen=True)
class GitEntrySource:
    """A fresh git checkout for one entry to consume — files, not bytes.

    One example, a ``resources`` entry reading ``kb/faq.csv`` out of a source
    whose ref moved since the last apply::

        GitEntrySource(
            checkout=GitCheckout(
                root=PosixPath("/tmp/acm-git-x9"),
                sha="4f2a9c1b8e7d6a5c4b3a2918f7e6d5c4b3a29187",
                url="https://code.example.com/team/content.git",
                ref="v1.2.0",
                members=(("100644", "kb/faq.csv", 812),),
            ),
            source_url="https://code.example.com/team/content.git",
            subpath="kb/faq.csv",
            moved_from="1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
            auth="git-prod",
            file_limit=104857600,      # FETCH_ENTRY_LIMITS["resources_file"]
        )

    Created by: ``apply/source_fetchers.GitSourceFetcher.fetch``.
    Consumed by: ``apply/entry_delivery.GitDelivery``, which is the only thing
    that wraps one.

    The object road hands back bytes because there was exactly one blob to
    read; the git road hands back a proven tree and lets the materialiser read
    it, because what "the entry's bytes" are is a *category* question this
    layer must not answer.
    """

    #: The fetched tree on disk, its members already enumerated and proven
    #: plain files. Shared with every other entry on the same ``(url, ref)``.
    checkout: GitCheckout
    #: The **repository** URL, substituted, with no ``git+`` prefix and no sha.
    #: :meth:`receipt_url` is what turns it into the receipt identity.
    source_url: str
    #: The composed path to read out of the tree: the source's ``subpath``
    #: then the entry's, e.g. ``"kb/faq.csv"``. ``None`` means the whole tree.
    subpath: Optional[str]
    #: The SHA the last apply recorded, when this apply resolved a different
    #: one — the non-strict road's "the ref moved" signal, surfaced by
    #: :meth:`moved_note`. ``None`` when the ref did not move, or had no
    #: baseline to move from.
    moved_from: Optional[str]
    #: The credential's **name**, never its value, threaded to the receipts so
    #: lineage attributes git-sourced bytes the way it does object-sourced
    #: ones. ``None`` for an anonymous fetch.
    auth: Optional[str] = None
    #: The category's per-entry byte cap, in bytes — the same
    #: ``FETCH_ENTRY_LIMITS`` number the object road enforces at the transport.
    #: The tree's readers refuse a member by its *declared* size against it.
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
        """The receipt identity for this entry's git-sourced bytes::

            "git+https://code.example.com/team/content.git"
            "@4f2a9c1b8e7d6a5c4b3a2918f7e6d5c4b3a29187:kb/faq.csv"

        Keyed on the *resolved* sha, so a moved ref is a different address and
        a bare source with no subpath ends in a trailing colon.
        """
        return git_receipt_url(self.source_url, self.checkout.sha, self.subpath)

    def moved_note(self) -> Optional[str]:
        """The non-strict road's report line about a moved ref, or ``None``::

            "ref moved: the last apply recorded 1a2b3c4d..., this one
             resolved 4f2a9c1b..."

        Surfaces as the entry's ``note`` on a **successful** row. Strict mode
        never reaches here: it refuses the entry instead.
        """
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

    Two implementations: :class:`BlobDelivery` (an HTTPS GET or one object
    read) and :class:`GitDelivery` (a proven checkout). What each member
    answers, per road:

    ===================  ======================  =========================
    Method               ``BlobDelivery``        ``GitDelivery``
    ===================  ======================  =========================
    ``is_tree()``        ``False``               ``True``
    ``members()``        the archive unpacked,   every file under the
                         or a stored tree        composed ``subpath``
                         decoded
    ``single()``         the whole blob          the one file ``subpath``
                                                 names
    ``note()``           the ``keep_last``       the moved-ref note
                         fallback reason
    ``digest()``         the content digest      ``None``; git declares no
                                                 digest
    ``receipt_url()``    ``https://…`` or        ``git+…@<sha>:<subpath>``
                         ``oss://…``
    ``auth()``           ``None``; the fetch     the credential name
                         already filed it
    ``source_url()``     the fetched URL         the repository URL
    ``content_type()``   what the source said    ``None``
    ``from_store()``     ``True`` on a store     always ``False``
                         hit
    ``needs_receipt()``  ``False``               ``True``
    ===================  ======================  =========================

    Which category asks which: ``skills`` alone calls ``is_tree``,
    ``source_url``, ``content_type`` and ``from_store``; ``resources`` calls
    ``members``; ``resources``, ``identity`` and ``cli_tools`` call
    ``single``; ``cli_tools`` alone calls ``digest``; and all four call
    ``note``, ``receipt_url``, ``auth`` and ``needs_receipt``.

    Created by: ``apply/source_fetchers`` (both fetchers), and by
    ``cli_tools/service`` around a ``fetch`` on the API-driven install road.
    Consumed by: every fetching materialiser — ``skills``, ``resources``,
    ``identity``, ``cli_tools``.

    Every member has exactly one caller family, named in its docstring. The
    surface is wide because the four fetching categories genuinely want
    different things out of one delivery — but it is not *open*: a member with
    no caller does not belong here.
    """

    @abstractmethod
    def is_tree(self) -> bool:
        """Did the source deliver a tree, or a single object?

        ``skills`` alone asks, to pick its validator. A capability question,
        deliberately, rather than a class check: what matters is the shape
        that arrived, not which implementation produced it.
        """
        ...

    @abstractmethod
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

    @abstractmethod
    def single(self) -> bytes:
        """The one file this entry delivers.

        ``resources``' file road, ``identity``, and ``cli_tools``. Raises
        :class:`EntryFetchError` when the source cannot name a single file —
        on the git road that is a checkout whose ``subpath`` names a directory
        or nothing.
        """
        ...

    @abstractmethod
    def note(self) -> Optional[str]:
        """The report line this delivery owes, or ``None``.

        A ``keep_last`` fallback's reason on the object road, a moved ref's
        note on the git road. Both answer "what should the entry's report row
        say about how these bytes arrived", which is why they share a name.
        """
        ...

    @abstractmethod
    def digest(self) -> Optional[str]:
        """The content address of what arrived, when one was computed.

        ``cli_tools``' declared-pin belt. ``None`` on a road that computed
        none, which the belt reads as "nothing to compare".
        """
        ...

    @abstractmethod
    def receipt_url(self) -> Optional[str]:
        """The W11 identity to file this entry's bytes under."""
        ...

    @abstractmethod
    def auth(self) -> Optional[str]:
        """The credential **name** the acquisition rode, for the receipt.

        Never a value. The lineage's answer to "which credential served this"
        is the same on both roads because both thread this.
        """
        ...

    @abstractmethod
    def source_url(self) -> Optional[str]:
        """Where the bytes came from, for callers that infer shape from it.

        ``skills``' archive-kind detection (``.tar.gz`` vs ``.zip``).
        """
        ...

    @abstractmethod
    def content_type(self) -> Optional[str]:
        """What the source said the bytes are, when it said anything.

        ``skills``' archive-kind detection again, as the second signal.
        """
        ...

    @abstractmethod
    def from_store(self) -> bool:
        """True when the platform's own copy answered and no network moved.

        ``skills`` carries it onto ``_SkillPackage`` so the report can tell a
        pinned fast path from a fresh fetch.
        """
        ...

    @abstractmethod
    def needs_receipt(self) -> bool:
        """Does the caller still owe this delivery's bytes to the store?

        All four fetching categories ask. ``False`` on the object road: the
        fetch filed what arrived on its way past, so a second write would be
        one entry with two receipts and ``keep_last`` reading whichever it
        found. ``True`` on the git road, where a checkout is not bytes and
        only the caller knows which bytes this entry actually delivers — the
        tree canonicalised, one file out of it, or a validated zip.
        """
        ...


@dataclass(frozen=True)
class BlobDelivery(EntryDelivery):
    """One object's bytes, however they were acquired.

    The object-store road, the API install road's plain URL, and every
    ``keep_last`` fallback — including a git one, whose stored bytes arrive
    here as a canonical tree blob rather than as a checkout::

        BlobDelivery(fetched=FetchedEntry(
            content=b"PK\x03\x04...",
            digest="sha256:9f86d081...",
            from_store=False,
            content_type="application/zip",
            source_url="oss://team-artifacts/tools/qc/v2.tgz",
        ))

    Differs from :class:`GitDelivery` in four answers: ``is_tree`` is
    ``False``, ``digest`` is a real content address, ``needs_receipt`` is
    ``False`` (the fetch already filed these bytes) and ``auth`` is ``None``
    (there is no second write for a credential name to ride on).
    """

    #: The bytes and their provenance. Every method here is a read of this.
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
        # Nothing downstream re-files these bytes (``needs_receipt`` is False),
        # so there is no second write for a credential name to ride on: the
        # fetch already filed the receipt, credential and all.
        return None

    def source_url(self) -> Optional[str]:
        return self.fetched.source_url

    def content_type(self) -> Optional[str]:
        return self.fetched.content_type

    def from_store(self) -> bool:
        return self.fetched.from_store

    def needs_receipt(self) -> bool:
        return False


@dataclass(frozen=True)
class GitDelivery(EntryDelivery):
    """A checked-out tree, read on the category's terms.

    The git road, and only a *successful* one: a git fetch that failed and fell
    back to ``keep_last`` comes back as a :class:`BlobDelivery` instead::

        GitDelivery(source=GitEntrySource(
            checkout=<the shared checkout>,
            source_url="https://code.example.com/team/content.git",
            subpath="kb/faq.csv",
            moved_from=None,
            auth="git-prod",
            file_limit=104857600,
        ))

    Differs from :class:`BlobDelivery` in four answers: ``is_tree`` is
    ``True``, ``digest`` is ``None`` (the schema refuses ``digest`` on a git
    source, so there is nothing for the pin belt to compare), ``needs_receipt``
    is ``True`` (a checkout is not bytes, and only the caller knows which bytes
    this entry delivers) and ``auth`` is the credential name, because those
    bytes are still owed to the store.
    """

    #: The checkout and the entry's view into it.
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

    def needs_receipt(self) -> bool:
        return True


# ── the shared mechanics ─────────────────────────────────────────────────────


def archive_refusal(unpack: object, strip_components: object) -> Optional[str]:
    """The archive fields a directory entry over ``oss`` must carry, or why not.

    Both arguments are the entry's declared values, **unvalidated** — typed
    ``object`` because they came straight out of parsed YAML and may be
    anything. ``None`` means they are acceptable; a string is the refusal::

        archive_refusal("zip", 0)        -> None
        archive_refusal("tar.gz", 1)     -> None
        archive_refusal(None, 0)         -> "a directory entry fetched over
                                             'oss' must declare
                                             'unpack: zip|tar.gz' — ..."
        archive_refusal(["zip"], 0)      -> the same refusal (unhashable, so
                                            the str check has to come first)
        archive_refusal("zip", -1)       -> "'strip_components' must be a
                                             non-negative integer"
        archive_refusal("zip", True)     -> the same; bool is rejected even
                                            though it is an int

    The values reach here from a ``resources`` entry::

        - path: data/prices/
          from: artifacts
          key: prices.tgz
          unpack: tar.gz          # <- unpack
          strip_components: 1     # <- strip_components

    Called by: ``materialisers/resources`` before the fetch, so a doomed entry
    costs no network against this apply's byte budget and lock TTL, and
    :meth:`BlobDelivery.members` after it, for the entry whose protocol that
    belt could not determine up front. Two calls of one function, never two
    rules — which is why it takes the two *values* rather than the entry: the
    pre-fetch caller has an entry, the delivery does not.
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

    ``archive`` is the fetched bytes. ``kind`` and ``strip_components`` are the
    entry's own ``unpack`` and ``strip_components``, already proven well-formed
    by :func:`archive_refusal` — ``kind`` is one of ``VALID_UNPACK``
    (``"zip"`` or ``"tar.gz"``) and ``strip_components`` a non-negative int.
    Both reach here from the entry via :meth:`BlobDelivery.members`, which is
    handed them by the materialiser that read them off the YAML.

    Answers either the members or a refusal string::

        unpack_members(<a tar.gz of qc-v2/bin/qc and qc-v2/README>,
                       "tar.gz", 1)
        # -> [("bin/qc", b"..."), ("README", b"...")]
        #    strip_components=1 dropped the "qc-v2/" prefix

        unpack_members(b"not an archive", "zip", 0)
        # -> "..." the UnpackError's own message

    Paths are relative and directories are structural, so only files come back.

    The bot is never a scratch space: ``unpack_archive`` writes only into a
    fresh temporary directory, so a bad or oversized archive (the member and
    unpacked-size limits live inside it) fails before anything is delivered.
    A refusal comes back as its reason string rather than an exception, keeping
    every failure in ``resolve``'s currency and the bot's tree untouched.
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


#: Marks the stored form of a git-delivered tree, and opens every blob
#: :func:`canonical_tree_bytes` produces.
#:
#: The framing after it is ``<path length>:<path><byte length>:<bytes>``
#: repeated, members sorted by path, all lengths in decimal ASCII and paths in
#: UTF-8. A two-file tree, byte for byte::
#:
#:     canonical_tree_bytes([("faq.csv", b"q,a\n"), ("a.txt", b"hi")])
#:     # -> b'acm-tree-v1\n5:a.txt2:hi7:faq.csv4:q,a\n'
#:     #       magic       ^^^^^^^^^^^^ "a.txt" (5 bytes) -> b"hi" (2 bytes)
#:     #                               ^^^^^^^^^^^^^^^^^ "faq.csv" (7) ->
#:     #                                                 b"q,a\n" (4)
#:
#: Note the sort: ``a.txt`` is emitted first although it was passed second.
#:
#: Self-describing on purpose: ``keep_last`` reads a receipt back as plain
#: bytes with no idea what shape they are, and "guess from the URL" is not
#: identification. The version digit is what lets the framing change later
#: without a stored copy being decoded under the wrong rules.
TREE_MAGIC = b"acm-tree-v1\n"


def canonical_tree_bytes(members: list[tuple[str, bytes]]) -> bytes:
    """One deterministic byte string standing for a whole delivered tree.

    Takes ``(relative path, bytes)`` pairs in any order and answers the framing
    :data:`TREE_MAGIC` documents, which shows a worked two-file example.

    Called by: ``materialisers/resources`` and ``materialisers/skills``, for the
    bytes a git-delivered entry owes the store.

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
    """The members back out, or ``None`` when these are not a canonical tree::

        decode_tree_bytes(b"acm-tree-v1\n5:a.txt2:hi7:faq.csv4:q,a\n")
        # -> [("a.txt", b"hi"), ("faq.csv", b"q,a\n")]

        decode_tree_bytes(b"PK\x03\x04...")   # a zip; no magic
        # -> None

    Called by: :meth:`BlobDelivery.members`, first thing, to tell a stored git
    tree from an archive.

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
            before = at
            colon = blob.index(b":", at)
            name_len = int(blob[at:colon])
            # A **negative** length is the interesting one, not a large one.
            # ``int(b"-3")`` parses happily, and a negative size moved the
            # cursor *backwards* — the loop then read the same frame forever,
            # hanging the worker thread with the apply's lock held while more
            # applies queued behind it. These bytes are attacker-reachable:
            # they are whatever a bucket served for a ``resources`` directory
            # entry, decoded before any archive validation runs.
            if name_len < 0:
                return None
            at = colon + 1
            name = blob[at : at + name_len].decode("utf-8")
            at += name_len
            colon = blob.index(b":", at)
            size = int(blob[at:colon])
            if size < 0:
                return None
            at = colon + 1
            if at + size > len(blob):
                return None
            members.append((name, blob[at : at + size]))
            at += size
            if at <= before:
                # Belt: every frame must consume at least one byte. Two
                # explicit sign checks are easy to add a third case beside and
                # forget; this makes non-termination structurally impossible
                # rather than impossible-by-enumeration.
                return None
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
