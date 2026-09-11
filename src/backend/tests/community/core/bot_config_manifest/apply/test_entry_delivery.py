"""The delivery seam: one type, two implementations, one honest discriminator.

What these pin is the *contract* four categories now depend on. The
materialiser tests exercise each category through it; these exercise the seam
itself, including the two orderings that are load-bearing rather than
incidental.
"""
from __future__ import annotations

import io
import tarfile

import pytest
from types import SimpleNamespace

from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import (
    BlobDelivery,
    EntryDelivery,
    EntryFetchError,
    FetchedEntry,
    GitDelivery,
    GitEntrySource,
    archive_refusal,
    canonical_tree_bytes,
    decode_tree_bytes,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchRefusedError,
)

from ._fakes import FakeManifestContent, make_context


def _blob(content: bytes = b"body", **kw) -> BlobDelivery:
    return BlobDelivery(
        FetchedEntry(
            content=content,
            digest=kw.pop("digest", "sha256:abc"),
            from_store=kw.pop("from_store", False),
            **kw,
        )
    )


def _tar_gz(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _git(
    *,
    files=(("skill.md", b"tree-bytes"),),
    body: bytes = b"one-file",
    subpath: str | None = "pkg",
    moved_from: str | None = None,
    auth: str | None = None,
    refuse: str | None = None,
    store=None,
) -> GitDelivery:
    def _read(subpath=None, file_limit=None):
        if refuse is not None:
            raise FetchRefusedError(refuse)
        return body

    return GitDelivery(
        GitEntrySource(
            checkout=SimpleNamespace(
                sha="c" * 40,
                root=None,
                files=lambda subpath=None, file_limit=None: list(files),
                read_file=_read,
            ),
            source_url="https://git.corp/x.git",
            subpath=subpath,
            moved_from=moved_from,
            auth=auth,
        ),
        # The store the git road is handed when its fetcher builds the
        # delivery: this is the road that still owes a write.
        store if store is not None else FakeManifestContent(),
    )


# ── the seam ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("delivery", [_blob(), _git()])
def test_both_implementations_satisfy_the_protocol(delivery):
    """Structural, not nominal — neither inherits from the Protocol.

    A member added to :class:`EntryDelivery` with no implementation behind it
    is exactly the drift this asserts against.
    """
    assert isinstance(delivery, EntryDelivery)


def test_the_two_roads_disagree_on_exactly_the_two_questions_that_differ():
    """``is_tree`` and what ``file`` does are the whole discriminating surface.

    Everything else is answered on both roads. ``needs_receipt`` used to be the
    second question and it was the caller's to ask; now the delivery answers it
    by *acting* — the git road writes, the object road does not — and the only
    question left on the seam is the one ``skills`` genuinely needs. If a third
    ever needs asking, this is where it becomes visible as a design decision
    rather than an ``isinstance`` sneaking back in.
    """
    blob, git = _blob(), _git(store=FakeManifestContent())
    ctx = make_context()
    assert blob.is_tree() is False
    assert git.is_tree() is True

    blob.file(ctx, b"body", category="skills", entry_identity="s1")
    git.file(ctx, b"body", category="skills", entry_identity="s1")
    assert git.store.store_calls and not hasattr(blob, "store")


# ── BlobDelivery ─────────────────────────────────────────────────────────────


def test_a_stored_tree_is_decoded_before_unpack_is_even_considered():
    """The ordering inside ``members`` is load-bearing, not an optimisation.

    ``keep_last`` standing in for a failed *git* fetch hands back the canonical
    tree as plain bytes. Asking about ``unpack`` first would send that entry
    down the archive road and refuse it for not declaring a field a git source
    may not carry — a transient outage turned into a category failure, with the
    bot's own last-good copy sitting right there.
    """
    members = [("a/b.md", b"one"), ("c.md", b"two")]
    delivery = _blob(canonical_tree_bytes(members))

    # No unpack declared — the archive road would refuse this outright.
    assert delivery.members(unpack=None, strip_components=0) == sorted(members)


def test_an_archive_unpacks_when_the_entry_declared_how():
    delivery = _blob(_tar_gz({"top/x.md": b"hello"}))
    assert delivery.members(unpack="tar.gz", strip_components=1) == [
        ("x.md", b"hello")
    ]


def test_an_undeclared_unpack_comes_back_as_a_reason_not_an_exception():
    """``resolve``'s currency is a reason string; an exception would abort the
    category where the contract wants one entry's failure."""
    refusal = _blob(b"not-a-tree").members(unpack=None, strip_components=0)
    assert isinstance(refusal, str)
    assert "unpack" in refusal


def test_a_corrupt_archive_is_a_reason_too():
    refusal = _blob(b"\x1f\x8b not really gzip").members(
        unpack="tar.gz", strip_components=0
    )
    assert isinstance(refusal, str)


def test_the_blob_road_answers_the_remaining_questions_off_its_payload():
    delivery = _blob(
        b"body",
        digest="sha256:feed",
        from_store=True,
        content_type="application/zip",
        fallback_reason="stood in (keep_last)",
        source_url="https://content.example/x.zip",
    )
    assert delivery.single() == b"body"
    assert delivery.digest() == "sha256:feed"
    assert delivery.from_store() is True
    assert delivery.content_type() == "application/zip"
    assert delivery.note() == "stood in (keep_last)"
    assert delivery.source_url() == "https://content.example/x.zip"


# ── GitDelivery ──────────────────────────────────────────────────────────────


def test_a_tree_ignores_unpack_because_put_already_refused_it():
    """``ARCHIVE_FIELDS_BY_KIND[GIT]`` is empty, so a git source carrying
    ``unpack`` never reaches a delivery. Ignoring the argument is safe by
    construction rather than by hope — and passing a nonsense value proves the
    tree road does not consult it."""
    delivery = _git(files=(("a.md", b"1"), ("b.md", b"2")))
    assert delivery.members(unpack="zip", strip_components=9) == [
        ("a.md", b"1"),
        ("b.md", b"2"),
    ]


def test_a_tree_that_names_no_single_file_refuses_through_the_seam():
    """The refusal identity gave up when it stopped asking the question itself.

    It must still arrive as an ``EntryFetchError`` carrying report-safe words,
    because a materialiser hands ``reason`` straight to ``ResolveFailure``.
    """
    delivery = _git(refuse="the source's 'subpath' must name a single file")
    with pytest.raises(EntryFetchError) as exc:
        delivery.single()
    assert "subpath" in exc.value.reason


def test_a_moved_ref_becomes_the_note_and_names_both_shas():
    delivery = _git(moved_from="b" * 40)
    note = delivery.note()
    assert note is not None and "b" * 40 in note and "c" * 40 in note


def test_a_tree_files_under_its_receipt_identity_and_credential_name():
    """What the caller used to assemble out of ``receipt_url`` and ``auth``,
    the delivery now puts on the receipt row itself."""
    content = FakeManifestContent()
    delivery = _git(auth="ci-token", subpath="pkg", store=content)

    digest = delivery.file(
        make_context(apply_id="apply-1"),
        b"canonical-zip",
        category="skills",
        entry_identity="s1",
        content_type="application/zip",
    )

    import hashlib

    assert digest == "sha256:" + hashlib.sha256(b"canonical-zip").hexdigest()
    call = content.store_calls[-1]
    assert call["credential_name"] == "ci-token"
    assert "c" * 40 in call["source_url"] and "pkg" in call["source_url"]
    assert call["apply_id"] == "apply-1"
    assert call["entry_identity"] == "s1"


def test_a_blob_files_nothing_and_answers_with_its_own_digest():
    """The object road filed what it read on its way past, so a second write
    would be one entry with two receipts and ``keep_last`` reading whichever it
    found.

    Structural, not merely observed: this road holds no store to write
    through, which is the strongest form the statement takes. The digest is
    the delivery's own rather than a hash of the caller's bytes — the receipt
    this entry answers for is the one already in the store — so the caller's
    bytes here are deliberately *different* ones, and the answer is still the
    fetch's digest. ``test_entry_fetch`` pins the same property end to end:
    one fetch, one receipt, `file` adds none.
    """
    delivery = _blob(b"body", digest="sha256:feed")

    digest = delivery.file(
        make_context(),
        b"some-other-bytes",
        category="skills",
        entry_identity="s1",
    )

    assert digest == "sha256:feed"
    assert not hasattr(delivery, "store")


def test_a_tree_offers_no_digest_for_the_pin_belt_to_compare():
    """Git bytes are addressed by their commit, and the schema refuses
    ``digest`` on a git source — so ``None`` is the honest answer, and the
    belt reads it as "nothing to compare"."""
    assert _git().digest() is None


# ── the canonical tree form ──────────────────────────────────────────────────


def test_the_canonical_form_round_trips_and_sorts():
    members = [("z.md", b"last"), ("a/b.md", b"first")]
    assert decode_tree_bytes(canonical_tree_bytes(members)) == sorted(members)


def test_two_different_trees_cannot_be_made_to_hash_alike():
    """Length-prefixed, not delimiter-joined: a delimiter is something a path
    or a payload could contain, and a tree that could be made to frame as
    another tree is a receipt that proves nothing."""
    assert canonical_tree_bytes([("a", b"b"), ("c", b"d")]) != canonical_tree_bytes(
        [("a", b"b\nc"), ("", b"d")]
    )


@pytest.mark.parametrize(
    "blob",
    [b"", b"PK\x03\x04 a real zip", canonical_tree_bytes([("a.md", b"0123456789")])[:-4]],
)
def test_what_is_not_a_canonical_tree_decodes_to_none(blob):
    """``None``, never an exception: the caller is asking "is this a stored
    tree or an archive?", and a shape it does not recognise is an answer. A
    truncated one is the same answer — a receipt that does not decode cleanly
    must not half-deliver a tree."""
    assert decode_tree_bytes(blob) is None


# ── the shared archive rule ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "unpack,strip,expected",
    [
        ("zip", 0, None),
        ("tar.gz", 2, None),
        (None, 0, "unpack"),
        ("rar", 0, "unpack"),
        (["zip"], 0, "unpack"),  # unhashable: must refuse, not raise
        ("zip", -1, "strip_components"),
        ("zip", True, "strip_components"),  # bool is an int, and not one here
        ("zip", "1", "strip_components"),
    ],
)
def test_the_archive_rule_is_one_rule_two_callers_ask(unpack, strip, expected):
    """``resources`` asks before the fetch so a doomed entry costs no network;
    ``BlobDelivery.members`` asks after, for the entry whose protocol that belt
    could not determine up front. Taking the two *values* rather than the entry
    is what lets both ask — the pre-fetch caller holds an entry, the delivery
    does not."""
    refusal = archive_refusal(unpack, strip)
    if expected is None:
        assert refusal is None
    else:
        assert refusal is not None and expected in refusal
