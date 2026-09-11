"""Tests for the per-entry fetch pipeline (``apply/source_resolver.py``, W5).

The pipeline is where the fetch-side waves meet: the object store and git as
transports, W3's named credentials, W11's platform copy. What these tests pin
is the *policy* on top of them — pinned entries read from the store, unpinned
entries re-read the source, ``keep_last`` reads the receipt only when it may —
and that a secret cannot ride out through an error.

W2's guarded HTTPS transport is no longer one of them. It served the
bare-string ``source:`` road, which had one caller left (``cli_tools``, whose
management API now takes an upload) and no way to be declared; the road and its
entry point are gone, and the policy above lives on the object road's own
fetcher, reached here through the front door the way production reaches it.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import (
    GitDelivery,
)
from agentclaw.community.core.bot_config_manifest.fetch.object_store import (
    ObjectStoreTarget,
)
from tests.community.core.bot_config_manifest.apply._fakes import FakeObjectStore
from agentclaw.community.core.bot_config_manifest.apply.source_resolver import (
    declared_protocol,
    EntryFetchError,
    DeclaredSourceResolver,
)
from agentclaw.community.core.bot_config_manifest.apply.source_fetchers import (
    ObjectStoreFetcher,
    object_receipt_url,
)
from agentclaw.community.core.bot_config_manifest.support_matrix import SourceKind
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitSourceSpec,
    git_receipt_url,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
)
from ._fakes import (
    FakeCredentials,
    FakeManifestContent,
    fetched_object,
    make_context,
)

BODY = b"soul-or-skill-bytes"


def _with_budget(ctx, budget):
    from dataclasses import replace

    return replace(ctx, budget=budget)
URL = "https://content.example/payload.bin"
DIGEST = "sha256:" + hashlib.sha256(BODY).hexdigest()


def _store_serving(content: FakeManifestContent) -> None:
    """One receipt already filed for ``URL`` holding ``BODY``."""
    content.store(
        fetched_object(BODY, url=URL, content_type="text/markdown"),
        scope=None,
        source_url=URL,
    )
    content.store_calls.clear()


_OBJ_AK = "LTAI5tTestKeyId"
_OBJ_ENDPOINT = "https://objects.internal.example"


class _AksKCredentials(FakeCredentials):
    """A credentials service whose one binding is an object-store credential.

    It hands back a real ``ObjectStoreTarget``, so the endpoint the fetcher
    reads comes off the *credential* here exactly as it does in production —
    a double that let the test choose the endpoint would erase the property
    these tests exist to pin.
    """

    def binding(self, *, name):
        binding = super().binding(name=name)
        binding.object_store_target = lambda bucket: ObjectStoreTarget(
            endpoint=_OBJ_ENDPOINT,
            bucket=bucket,
            access_key_id=_OBJ_AK,
            secret_access_key="the-secret-half",
            region="cn-shanghai",
        )
        return binding


@pytest.fixture
def objects_rig():
    """A pipeline wired to an in-memory object store — the test double that
    gives every ``ObjectFetchStatus`` a shape a consumer test can drive."""
    content = FakeManifestContent()
    objects = FakeObjectStore()
    pipeline = DeclaredSourceResolver(content, _AksKCredentials(), objects)
    return content, objects, pipeline


@pytest.fixture
def rig():
    content = FakeManifestContent()
    credentials = FakeCredentials()
    return content, credentials, DeclaredSourceResolver(content, credentials, FakeObjectStore())


#: The object road's coordinates, as a document declares them, and the address
#: a receipt for them is filed under. The endpoint and the key pair are absent
#: on purpose: they come off the credential in production, which is what
#: driving these tests through the front door proves.
_OSS_SOURCE = {
    "protocol": "oss",
    "bucket": "b",
    "key": "k",
    "auth": "oss-cred",
}
_ADDRESS = object_receipt_url("b", "k")


def _store_holding(content: FakeManifestContent, body: bytes = BODY) -> None:
    """One receipt already filed for the object address, holding ``body``."""
    content.store(
        fetched_object(body, url=_ADDRESS, content_type=None),
        scope=None,
        source_url=_ADDRESS,
    )
    content.store_calls.clear()


def _oss_ctx(**kwargs):
    """A context whose session declares :data:`_OSS_SOURCE` as ``"s"``."""
    return make_context(
        source_session=_session(_ScriptedGit(), sources={"s": _OSS_SOURCE}),
        **kwargs,
    )


def _acquire(pipeline, *, ctx=None, digest=None, keep_last=False):
    """One object-road fetch, driven the way production reaches it.

    Through the front door with a declared ``oss`` source — so the credential
    resolves the target, exactly as it does in an apply — and unwrapped back to
    the :class:`FetchedEntry` these tests make their statements about. The road
    itself lives on ``source_fetchers.ObjectStoreFetcher``; reaching it through
    the dispatcher is what keeps these tests pinned to the pipeline rather than
    to one class's private method.
    """
    entry = {"from": "s", "on_fetch_failure": "keep_last" if keep_last else "fail"}
    if digest is not None:
        entry["digest"] = digest
    return pipeline.resolve(
        ctx if ctx is not None else _oss_ctx(),
        entry=entry,
        category="identity",
        entry_identity="data/faq.csv",
    ).fetched


# --- the store-first policy, on the road that still has a network ---------
#
# These cases used to drive ``DeclaredSourceResolver.fetch``, the HTTPS-GET road that no
# longer exists. The policy they pin is not the transport's — it is the
# pipeline's, and ``acquire_object`` is where it lives now: pinned entries read
# from the platform's copy, unpinned ones re-read, ``keep_last`` answers only
# when it may, and every store fault is *this entry's* failure. The ones that
# were about the URL transport itself (per-hop prefix authorization, a refused
# address, a header injector) went with it: they pinned ``GuardedFetcher``'s
# seams, which this module no longer has.


def test_a_pinned_entry_with_a_matching_receipt_is_served_from_the_store(
    objects_rig,
):
    content, objects, pipeline = objects_rig
    _store_holding(content)

    result = _acquire(pipeline, digest=DIGEST)
    # No read: the platform's own copy answers for the pinned bytes.
    assert objects.calls == []
    assert result.from_store is True
    assert result.content == BODY
    assert result.digest == DIGEST


def test_a_pinned_entry_with_a_mismatched_receipt_rereads(objects_rig):
    content, objects, pipeline = objects_rig
    _store_holding(content)
    # The source legitimately rotated: it now holds bytes pinned by a NEW
    # digest, so the platform's old receipt for this address is stale, not
    # "last".
    rotated = b"rotated-bytes"
    rotated_digest = "sha256:" + hashlib.sha256(rotated).hexdigest()
    objects.put("b", "k", rotated, access_key_id=_OBJ_AK)

    result = _acquire(pipeline, digest=rotated_digest)
    assert len(objects.calls) == 1
    assert result.from_store is False
    assert result.digest == rotated_digest
    assert len(content.store_calls) == 1


def test_an_unpinned_entry_rereads_even_when_a_receipt_exists(objects_rig):
    content, objects, pipeline = objects_rig
    _store_holding(content)
    objects.put("b", "k", BODY, access_key_id=_OBJ_AK)

    result = _acquire(pipeline)
    # No pin means "whatever is there now": the source is re-read so an apply
    # converges to it, never to our own memory of it.
    assert len(objects.calls) == 1
    assert result.from_store is False


def test_a_pinned_entry_with_a_matching_receipt_survives_a_dead_source(
    objects_rig,
):
    """A pinned entry with a matching receipt never reaches the store at all —
    so a source that is DOWN between applies costs nothing: content addressing
    makes the stored bytes *the* declared bytes regardless of availability.
    This is the store-hit fast path, NOT a keep_last fallback (no read was
    attempted, none failed), and it carries no note: silence here is legitimate
    — the pinned fast path is exactly what convergence looks like, while a
    keep_last fallback is what "the source failed" looks like and must be
    reported. The two are distinguished by the mark."""
    content, objects, pipeline = objects_rig
    _store_holding(content)
    objects.make_unavailable("b")

    result = _acquire(pipeline, digest=DIGEST, keep_last=True)
    assert result.from_store is True
    assert result.content == BODY
    assert objects.calls == []  # never reached the dead source
    assert result.fallback_reason is None  # a fast path, not a fallback


def test_keep_last_with_no_receipt_fails(objects_rig):
    _, objects, pipeline = objects_rig
    objects.make_unavailable("b", "endpoint unreachable")

    with pytest.raises(EntryFetchError) as excinfo:
        _acquire(pipeline, keep_last=True)
    # The store's own words, which carry no caller or source data.
    assert "endpoint unreachable" in excinfo.value.reason


def test_keep_last_never_supplies_bytes_that_disagree_with_a_pin(objects_rig):
    content, objects, pipeline = objects_rig
    _store_holding(content)
    objects.make_unavailable("b")

    with pytest.raises(EntryFetchError) as excinfo:
        _acquire(pipeline, digest="sha256:" + "1" * 64, keep_last=True)
    # The receipt is stale, not "last": refused rather than silently pinning
    # bytes the declaration never named.
    assert "endpoint unreachable" in excinfo.value.reason


def test_a_read_failure_without_keep_last_fails_the_entry(objects_rig):
    _, objects, pipeline = objects_rig
    objects.make_unavailable("b", "endpoint unreachable")

    with pytest.raises(EntryFetchError) as excinfo:
        _acquire(pipeline)
    assert "endpoint unreachable" in excinfo.value.reason


def test_a_missing_credential_fails_with_the_name_and_no_read(objects_rig):
    """The credential is resolved before the store is touched, and a name that
    no longer exists is configuration drift: loud, named, and never masked."""
    content, objects, _ = objects_rig
    pipeline = DeclaredSourceResolver(
        content, FakeCredentials(missing={"ghost"}), objects
    )
    ctx = make_context(source_session=_session(_ScriptedGit(), sources={
        "s": {"protocol": "oss", "bucket": "b", "key": "k", "auth": "ghost"},
    }))

    with pytest.raises(EntryFetchError) as excinfo:
        pipeline.resolve(ctx, entry={"from": "s"}, category="identity")
    assert "ghost" in excinfo.value.reason
    assert objects.calls == []


def test_the_store_receives_the_actor_as_modifier(objects_rig):
    content, objects, pipeline = objects_rig
    objects.put("b", "k", BODY, access_key_id=_OBJ_AK)
    _acquire(pipeline)
    assert content.store_calls[0]["modifier"] == "u_actor"


# --- the P0-2 translation family: store faults are the ENTRY's failures ---


def test_a_pinned_blob_that_went_missing_heals_via_a_reread(objects_rig):
    """A pinned store-hit whose blob is gone (the store lost the file) is a
    self-repairing cache miss, not a caller-visible failure: the pin is
    byte-provable, the re-read reacquires exactly those bytes, and the re-file
    heals the address. The result reads as an ordinary read — from_store False,
    no fallback note: nothing FAILED."""
    content, objects, pipeline = objects_rig
    _store_holding(content)
    content.missing_blobs.add(DIGEST)
    objects.put("b", "k", BODY, access_key_id=_OBJ_AK)

    result = _acquire(pipeline, digest=DIGEST)
    assert objects.calls, "the missing blob must be reacquired"
    assert result.content == BODY
    assert result.digest == DIGEST
    assert result.from_store is False
    assert result.fallback_reason is None
    # …and the address healed: the blob is readable again.
    assert content.read(DIGEST) == BODY


def test_a_pinned_blob_that_is_corrupt_loudly_fails_the_entry(objects_rig):
    """A blob that exists but fails its own digest is disk-side damage — a
    hit a re-read CANNOT heal (the dedup write skips same-size files) — so
    it stays the 500-family failure it is, on this entry, with its reason;
    never a silent skip and never a wrapped whole-category abort."""
    content, _, pipeline = objects_rig
    _store_holding(content)
    content.corrupt_blobs.add(DIGEST)

    with pytest.raises(EntryFetchError) as excinfo:
        _acquire(pipeline, digest=DIGEST)
    assert "could not be read" in excinfo.value.reason
    assert "fails its own digest" in excinfo.value.reason


def test_a_store_side_refusal_of_the_lookup_is_the_entrys_error(objects_rig):
    content, _, pipeline = objects_rig
    from agentclaw.community.core.bot_config_manifest.content.errors import (
        ContentStoreError,
    )

    content.lookup_fault = ContentStoreError(
        "provenance fetched_url exceeds the 2048-char column: length 2200"
    )
    with pytest.raises(EntryFetchError) as excinfo:
        _acquire(pipeline, digest="sha256:" + "0" * 64)
    # The store's own message (never the address), as this entry's failure.
    assert "2048-char column" in excinfo.value.reason


def test_a_store_side_refusal_of_the_filing_is_the_entrys_error(objects_rig):
    """The refusal that lands AFTER the bytes were acquired. It fails ONE entry
    with the store's words, not the whole category under a wrapped 'resolve
    failed' surprise."""
    content, objects, pipeline = objects_rig
    from agentclaw.community.core.bot_config_manifest.content.errors import (
        ContentStoreError,
    )

    objects.put("b", "k", BODY, access_key_id=_OBJ_AK)
    content.store_fault = ContentStoreError(
        "provenance fetched_url exceeds the 2048-char column: length 2200"
    )
    with pytest.raises(EntryFetchError) as excinfo:
        _acquire(pipeline)
    assert "could not be filed" in excinfo.value.reason
    assert "2048-char column" in excinfo.value.reason


def test_a_keep_last_read_failure_names_both_halves(objects_rig):
    """The source failed, the fallback copy ALSO could not be read: the
    entry's error carries both reasons — drop either and the caller fixes
    the wrong thing (the audit called this swallowing)."""
    content, objects, pipeline = objects_rig
    _store_holding(content)
    content.missing_blobs.add(DIGEST)
    objects.make_unavailable("b", "endpoint unreachable")

    with pytest.raises(EntryFetchError) as excinfo:
        _acquire(pipeline, digest=DIGEST, keep_last=True)
    assert "endpoint unreachable" in excinfo.value.reason
    assert "keep_last fallback copy could not be read" in excinfo.value.reason


def test_a_time_exhausted_budget_refuses_before_touching_the_network(
    objects_rig,
):
    """The deadline is checked before the read — the audit's scenario was
    entries-long fetching that outran the apply-lock TTL and let the reaper
    hand a live apply's lock to a second one, so an exhausted budget must
    end the apply in bounded time with a named reason."""
    _, objects, pipeline = objects_rig
    from agentclaw.community.core.bot_config_manifest.apply.budget import (
        ApplyFetchBudget,
    )

    objects.put("b", "k", BODY, access_key_id=_OBJ_AK)
    ctx = _with_budget(
        _oss_ctx(), ApplyFetchBudget(deadline=0.0, total_bytes=10**9)
    )

    with pytest.raises(EntryFetchError) as excinfo:
        _acquire(pipeline, ctx=ctx)
    assert "exhausted (time)" in excinfo.value.reason
    assert objects.calls == []


def test_a_byte_exhausted_budget_refuses_the_next_entry(objects_rig):
    """Bytes charge per read; the cap stops the N+1st entry, not the first —
    per-entry caps stay the store client's own business."""
    _, objects, pipeline = objects_rig
    from agentclaw.community.core.bot_config_manifest.apply.budget import (
        ApplyFetchBudget,
    )

    objects.put("b", "k", BODY, access_key_id=_OBJ_AK)
    budget = ApplyFetchBudget(deadline=1e18, total_bytes=len(BODY), clock=lambda: 0.0)
    ctx = _with_budget(_oss_ctx(), budget)

    first = _acquire(pipeline, ctx=ctx)
    assert first.content == BODY  # the first read fits exactly

    with pytest.raises(EntryFetchError) as excinfo:
        _acquire(pipeline, ctx=ctx)
    assert "exhausted (bytes)" in excinfo.value.reason
    # Only the FIRST read reached the store; the refused one never did — and
    # note the first read's receipt does NOT serve the second (the receipt
    # exists, but an unpinned entry's re-read is the point).
    assert len(objects.calls) == 1


def test_the_funnel_requires_a_category_by_keyword():
    """Linkage by default is linkage by accident: a call site that forgets
    its category would file an unattributed receipt and take the default
    cap. Reject the omission loudly (type analysis flagged this as one of
    the PR's two blocking-level type issues)."""
    import inspect

    for entry_point in (DeclaredSourceResolver.resolve, ObjectStoreFetcher._acquire):
        category = inspect.signature(entry_point).parameters["category"]
        assert category.kind is inspect.Parameter.KEYWORD_ONLY
        assert category.default is inspect.Parameter.empty


# --- the W7 declared-source front door: resolve ---

GIT_URL = "https://git.corp/repo.git"
_FAKE_SHA = "a" * 40


class _ScriptedGit:
    def __init__(self, *, sha: str = _FAKE_SHA, error: Exception | None = None):
        self.specs: list[GitSourceSpec] = []
        self.headers: list[dict] = []
        self._sha = sha
        self._error = error

    def fetch(self, spec, *, headers=None):
        self.specs.append(spec)
        self.headers.append(dict(headers or {}))
        if self._error:
            raise self._error
        return SimpleNamespace(
            root=None, sha=self._sha, url=spec.url, ref=spec.ref,
            members=(("100644", spec.subpath or "pkg/skill.md", 11),),
            tree_bytes=11,
            files=lambda subpath=None, file_limit=None: [("skill.md", b"file-bytes")],
            read_file=lambda subpath=None, file_limit=None: b"file-bytes",
        )


def _session(git, *, sources=None, baselines=None):
    return SourceSession(
        sources=sources or {}, baselines=baselines or {}, git=git
    )


def test_a_named_oss_source_reads_through_the_object_store(objects_rig):
    """The oss road end to end, and the assertion that matters most about it:
    **there is no URL transport to reach.** That transport existed to make a
    tenant-supplied URL safe; the roads that remain have no URL, so the
    pipeline no longer holds one at all and an address from somewhere it should
    not have come from has nothing to travel over."""
    _, objects, pipeline = objects_rig
    objects.put("named-bucket", "assets/logo.png", BODY, access_key_id=_OBJ_AK)
    ctx = make_context(
        source_session=_session(_ScriptedGit(), sources={
            "cdn": {
                "protocol": "oss",
                "bucket": "named-bucket",
                "key": "assets/",
                "auth": "oss-cred",
            },
        })
    )
    result = pipeline.resolve(
        ctx, entry={"from": "cdn", "key": "logo.png"}, category="identity"
    )
    assert result.single() == BODY
    assert not hasattr(pipeline, "_fetcher")  # no URL transport, at all
    target, key, _ = objects.calls[0]
    # The source's key is a prefix and the entry's composes onto it, the same
    # rule ``subpath`` follows on the git road.
    assert (target.bucket, key) == ("named-bucket", "assets/logo.png")


def test_the_document_names_the_bucket_and_the_credential_names_the_host(
    objects_rig,
):
    """The security property this road holds by construction.

    A manifest chooses *what* to read. The endpoint comes off the credential
    row, so nothing a document can write moves the host its credential is
    presented to — which is why ``allowed_prefixes`` stopped being required
    for this mechanism.
    """
    _, objects, pipeline = objects_rig
    objects.put("b", "k", BODY, access_key_id=_OBJ_AK)
    ctx = make_context(source_session=_session(_ScriptedGit(), sources={
        "s": {"protocol": "oss", "bucket": "b", "key": "k", "auth": "oss-cred"},
    }))
    pipeline.resolve(ctx, entry={"from": "s"}, category="identity")
    assert objects.calls[0][0].endpoint == _OBJ_ENDPOINT


@pytest.mark.parametrize(
    "break_it,expected",
    [
        # The bucket exists and the key does not. Removing the object rather
        # than the bucket matters: an unknown bucket answers DENIED, since it
        # is not distinguishable from one this credential may not see.
        (lambda o: o.buckets["b"].objects.pop("k"), "no such object"),
        (lambda o: setattr(o.buckets["b"], "access_key_id", "rotated-away"), "not authorized"),
        (
            lambda o: o.buckets["b"].objects.__setitem__("k", b"x" * (1024 * 1024 + 1)),
            "exceeds",
        ),
    ],
)
def test_a_refusal_is_never_masked_by_keep_last(objects_rig, break_it, expected):
    """NOT_FOUND, DENIED and TOO_LARGE are the document's or the credential's
    fault. ``keep_last`` exists for an unreachable store, and a denied
    credential quietly serving last apply's bytes for a year is exactly what
    that ruling prevents.

    **A stored copy is filed first, on purpose.** Asserting the refusal with
    nothing in the store would pass against code that treated these as
    failures — there would be nothing to fall back to either way. The receipt
    is what makes this test able to tell the two rulings apart.
    """
    _, objects, pipeline = objects_rig
    objects.put("b", "k", BODY, access_key_id=_OBJ_AK)
    ctx = make_context(source_session=_session(_ScriptedGit(), sources={
        "s": {"protocol": "oss", "bucket": "b", "key": "k", "auth": "oss-cred"},
    }))
    entry = {"from": "s", "on_fetch_failure": "keep_last"}
    # The first apply succeeds and files the receipt a fallback would read.
    assert pipeline.resolve(ctx, entry=entry, category="identity").single() == BODY

    break_it(objects)
    with pytest.raises(EntryFetchError, match=expected):
        pipeline.resolve(ctx, entry=entry, category="identity")


def test_an_unreachable_store_is_a_failure_keep_last_may_answer(objects_rig):
    """The one status that is the transport rather than the document."""
    _, objects, pipeline = objects_rig
    objects.put("b", "k", BODY, access_key_id=_OBJ_AK)
    ctx = make_context(source_session=_session(_ScriptedGit(), sources={
        "s": {"protocol": "oss", "bucket": "b", "key": "k", "auth": "oss-cred"},
    }))
    # First apply files the receipt the second one falls back to.
    pipeline.resolve(ctx, entry={"from": "s"}, category="identity")
    objects.make_unavailable("b")

    result = pipeline.resolve(
        ctx,
        entry={"from": "s", "on_fetch_failure": "keep_last"},
        category="identity",
    )
    assert result.single() == BODY
    assert result.from_store() is True
    assert result.note() and "keep_last" in result.note()


def test_resolve_gives_the_git_road_a_checkout(rig):
    _, credentials, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(
        source_session=_session(git, sources={
            "app": {"protocol": "git", "url": GIT_URL, "ref": "main", "subpath": "pkg"},
        })
    )
    decl = pipeline.resolve(
        ctx, entry={"from": "app"}, category="skills", entry_identity="s1"
    )
    assert isinstance(decl, GitDelivery)
    assert decl.source.checkout.sha == _FAKE_SHA
    assert decl.source.files() == [("skill.md", b"file-bytes")]
    # No named credential on this source: none was asked of W3, and the
    # session recorded the resolution the report will carry.
    assert credentials.binding_calls == []
    assert ctx.source_session.resolution_records()[0].resolved_sha == _FAKE_SHA


def test_resolve_refuses_a_from_that_names_nothing(rig):
    _, _, pipeline = rig
    ctx = make_context(source_session=_session(_ScriptedGit()))
    with pytest.raises(EntryFetchError, match="not declared"):
        pipeline.resolve(ctx, entry={"from": "ghost"}, category="skills")


def test_resolve_missing_session_is_loud(rig):
    _, _, pipeline = rig
    ctx = make_context()
    with pytest.raises(EntryFetchError, match="no source session"):
        pipeline.resolve(ctx, entry={"from": "x"}, category="skills")


def test_strict_refuses_when_the_ref_moved(rig):
    _, _, pipeline = rig
    git = _ScriptedGit()
    # The baseline is keyed on the repository and the ref, not on what the
    # document called the source — so the same pair, resolving to a different
    # commit, is the one thing strict mode refuses.
    ctx = make_context(
        source_session=_session(
            git, baselines={(GIT_URL, "main", "strict"): "b" * 40}
        )
    )
    with pytest.raises(EntryFetchError, match="moved"):
        pipeline.resolve(
            ctx,
            entry={"source": {"protocol": "git", "url": GIT_URL, "ref": "main", "mode": "strict"}},
            category="skills",
        )
    # The refused move was NOT adopted: this apply's report records no
    # resolution for the source, so the next apply's baseline is still the
    # one this refusal was checked against — strict mode refuses every
    # apply until the document re-pins, not just the first.
    assert ctx.source_session.resolution_records() == ()


def test_non_strict_records_the_move_in_the_note(rig):
    _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(
        source_session=_session(
            git, baselines={(GIT_URL, "main", "non_strict"): "b" * 40}
        )
    )
    decl = pipeline.resolve(
        ctx,
        entry={"source": {"protocol": "git", "url": GIT_URL, "ref": "main"}},
        category="skills",
    )
    assert isinstance(decl, GitDelivery)
    assert decl.note() and "b" * 40 in decl.note()
    assert "a" * 40 in decl.note()


def test_strict_on_the_first_apply_has_no_opinion(rig):
    _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(source_session=_session(git))  # no baselines
    decl = pipeline.resolve(
        ctx,
        entry={"source": {"protocol": "git", "url": GIT_URL, "ref": "main", "mode": "strict"}},
        category="skills",
    )
    assert isinstance(decl, GitDelivery)
    assert decl.note() is None


def test_an_inline_source_is_named_url_at_ref(rig):
    """An inline declaration has no name to report under, so it is named by
    the repository and the ref it asked for. The URL alone would not do: two
    entries reading one repository at two refs would collapse into one row
    naming neither of them, and a reader could not tell which ref the sha
    belonged to."""
    _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(source_session=_session(git))
    for ref in ("a", "b"):
        pipeline.resolve(
            ctx,
            entry={"source": {"protocol": "git", "url": GIT_URL, "ref": ref}},
            category="skills",
        )
    records = ctx.source_session.resolution_records()
    assert [r.name for r in records] == [f"{GIT_URL}@a", f"{GIT_URL}@b"]
    # The url rides its own field, so a reader never has to split the name.
    assert [(r.url, r.ref) for r in records] == [(GIT_URL, "a"), (GIT_URL, "b")]


def test_an_inline_strict_refusal_names_the_ref_it_refused(rig):
    """The refusal message uses the display, which for an inline source now
    says which ref moved — the difference between "this repository moved" and
    a sentence a reader can act on when the document names it twice."""
    _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(
        source_session=_session(
            git, baselines={(GIT_URL, "main", "strict"): "b" * 40}
        )
    )
    with pytest.raises(EntryFetchError, match=f"{GIT_URL}@main"):
        pipeline.resolve(
            ctx,
            entry={"source": {"protocol": "git", "url": GIT_URL, "ref": "main",
                              "mode": "strict"}},
            category="skills",
        )


def test_two_names_over_one_repository_are_two_rows_and_one_baseline(rig):
    """Report rows are per declaration; baselines are per ``(url, ref, mode)``.
    A document that names one repository twice at one mode gets both names back
    in the report — each author finds the name they wrote — and one checkout,
    one sha, and one baseline key behind them."""
    _, _, pipeline = rig
    git = _ScriptedGit()
    session = _session(git, sources={
        "content": {"protocol": "git", "url": GIT_URL, "ref": "main"},
        "docs": {"protocol": "git", "url": GIT_URL, "ref": "main"},
    })
    ctx = make_context(source_session=session)
    for name in ("content", "docs"):
        pipeline.resolve(ctx, entry={"from": name}, category="skills")
    records = session.resolution_records()
    assert [r.name for r in records] == ["content", "docs"]
    assert {r.resolved_sha for r in records} == {_FAKE_SHA}
    # One key, so the next apply reads one baseline for both rows — the apply
    # service's own test pins that the collapse survives the report round trip.
    assert {(r.url, r.ref, r.mode) for r in records} == {
        (GIT_URL, "main", "non_strict")
    }
    assert len(git.specs) == 1, "one checkout per (url, ref) per apply"


def test_a_non_strict_alias_cannot_advance_a_strict_pin(rig):
    """One repository, one ref, declared twice at two modes — and the pin holds.

    This configuration is legal and not even exotic: an author says "these
    entries may follow the branch, that one may not", and both read the same
    repository. When the ref moves, the lax declaration delivers the new commit
    and records it; the pinned one refuses.

    The danger is what the *next* apply then reads. If both declarations shared
    a baseline, the sha the lax one recorded would become the pin's baseline,
    and the next apply — with nothing in the document changed — would hand the
    pinned entry the very commit it had just rejected. That is strict mode
    degraded to "refuse each move exactly once, then deliver it", which is the
    failure the adopt-after-the-gate ordering exists to prevent; it would just
    have come in sideways, through a different declaration, one apply later.

    ``mode`` is in the baseline key so the two keep separate histories.
    """
    _, _, pipeline = rig
    old = "b" * 40
    sources = {
        "pinned": {"protocol": "git", "url": GIT_URL, "ref": "main",
                   "mode": "strict"},
        "loose": {"protocol": "git", "url": GIT_URL, "ref": "main",
                  "mode": "non_strict"},
    }

    # Apply N: the ref has moved off `old`, and both declarations see it.
    git = _ScriptedGit()
    session = _session(
        git, sources=sources,
        baselines={
            (GIT_URL, "main", "strict"): old,
            (GIT_URL, "main", "non_strict"): old,
        },
    )
    ctx = make_context(source_session=session)

    loose = pipeline.resolve(ctx, entry={"from": "loose"}, category="skills")
    assert isinstance(loose, GitDelivery)
    assert loose.note() and old in loose.note()  # delivered, and the move noted
    with pytest.raises(EntryFetchError, match="moved"):
        pipeline.resolve(ctx, entry={"from": "pinned"}, category="skills")

    # The report carries the lax delivery — provenance is not sacrificed — but
    # it is stamped with the mode it was resolved under, and the refused
    # declaration adopted nothing.
    rows = session.resolution_records()
    assert [(r.name, r.mode, r.resolved_sha) for r in rows] == [
        ("loose", "non_strict", _FAKE_SHA)
    ]

    # Apply N+1, document unchanged: the baselines the service rebuilds from
    # that report leave the strict key untouched, so the pin still refuses.
    rebuilt = {(r.url, r.ref, r.mode): r.resolved_sha for r in rows}
    # Stated as an equality rather than "the strict key is absent": absent is
    # also what an empty map gives, and a map that silently stopped carrying
    # the mode would satisfy the weaker form for the wrong reason.
    assert rebuilt == {(GIT_URL, "main", "non_strict"): _FAKE_SHA}
    next_session = _session(
        git, sources=sources,
        baselines={**{(GIT_URL, "main", "strict"): old}, **rebuilt},
    )
    with pytest.raises(EntryFetchError, match="moved"):
        pipeline.resolve(
            make_context(source_session=next_session),
            entry={"from": "pinned"},
            category="skills",
        )


def test_strict_passes_when_the_document_re_pins_the_ref(rig):
    """Editing ``ref`` is how a strict source is advanced, and it has to be:
    a baseline is a fact about a ``(url, ref)`` pair, and the pair the
    document now names is one no apply has resolved yet. Keyed on the source's
    name instead, this could never pass — the name did not change — and the
    only way out of a strict pin would be to flip the source to
    ``non_strict``, apply once, and flip it back."""
    _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(
        source_session=_session(
            git, baselines={(GIT_URL, "v1", "strict"): "b" * 40}
        )
    )
    decl = pipeline.resolve(
        ctx,
        entry={"source": {"protocol": "git", "url": GIT_URL, "ref": "v2",
                          "mode": "strict"}},
        category="skills",
    )
    assert isinstance(decl, GitDelivery)
    # Not a move: nothing is noted, and the new pair is what this apply now
    # stands behind, so the next apply pins against v2.
    assert decl.note() is None
    recorded = ctx.source_session.resolution_records()
    assert [(r.url, r.ref, r.resolved_sha) for r in recorded] == [
        (GIT_URL, "v2", _FAKE_SHA)
    ]


def test_strict_passes_when_the_document_re_points_the_url(rig):
    """The other half of the same rule. A baseline from one repository has no
    standing over another — keyed on the name, a re-pointed ``url`` would
    silently inherit the old repository's SHA and refuse a commit that never
    could have matched it."""
    _, _, pipeline = rig
    git = _ScriptedGit()
    other = "https://git.corp/other.git"
    ctx = make_context(
        source_session=_session(
            git, baselines={(GIT_URL, "main", "strict"): "b" * 40}
        )
    )
    decl = pipeline.resolve(
        ctx,
        entry={"source": {"protocol": "git", "url": other, "ref": "main",
                          "mode": "strict"}},
        category="skills",
    )
    assert isinstance(decl, GitDelivery)
    assert decl.note() is None
    recorded = ctx.source_session.resolution_records()
    assert [(r.url, r.ref) for r in recorded] == [(other, "main")]


def test_a_sha_shaped_ref_trips_neither_branch(rig):
    """What the schema doc has always promised: ``mode`` is "accepted but
    inert" on a ``ref`` that is already a commit. It holds by construction
    rather than by a special case — a SHA resolves to itself, so the pair
    ``(url, <sha>)`` always answers with the sha its baseline holds."""
    _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(
        source_session=_session(
            git, baselines={(GIT_URL, _FAKE_SHA, "strict"): _FAKE_SHA}
        )
    )
    decl = pipeline.resolve(
        ctx,
        entry={"source": {"protocol": "git", "url": GIT_URL, "ref": _FAKE_SHA,
                          "mode": "strict"}},
        category="skills",
    )
    assert isinstance(decl, GitDelivery)
    assert decl.note() is None


def test_non_strict_does_not_call_a_re_pin_a_move(rig):
    """The note answers "the ref moved under you". A document that edited its
    own ``ref`` moved it deliberately and does not need telling — and a note
    naming the previous ref's commit as what this one drifted from would be
    describing a drift that never happened."""
    _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(
        source_session=_session(
            git, baselines={(GIT_URL, "v1", "non_strict"): "b" * 40}
        )
    )
    decl = pipeline.resolve(
        ctx,
        entry={"source": {"protocol": "git", "url": GIT_URL, "ref": "v2"}},
        category="skills",
    )
    assert isinstance(decl, GitDelivery)
    assert decl.note() is None


def test_digest_on_a_git_source_is_refused(rig):
    _, _, pipeline = rig
    ctx = make_context(source_session=_session(_ScriptedGit()))
    with pytest.raises(EntryFetchError, match="digest"):
        pipeline.resolve(
            ctx,
            entry={"source": {"protocol": "git", "url": GIT_URL, "ref": "main"},
                   "digest": "sha256:" + "0" * 64},
            category="skills",
        )


def test_git_keep_last_falls_back_to_the_baseline_receipt(rig):
    content, credentials, pipeline = rig
    old_sha = "b" * 40
    baseline_url = git_receipt_url(GIT_URL, old_sha, "pkg")
    content.store(
        fetched_object(b"stored-tree-zip", url=baseline_url,
                       content_type="application/zip"),
        scope=None, source_url=baseline_url,
    )
    git = _ScriptedGit(error=FetchFailedError("git fetch failed"))
    ctx = make_context(source_session=_session(
        git,
        sources={"app": {"protocol": "git", "url": GIT_URL, "ref": "main", "subpath": "pkg"}},
        baselines={(GIT_URL, "main", "non_strict"): old_sha},
    ))
    result = pipeline.resolve(
        ctx,
        entry={"from": "app", "on_fetch_failure": "keep_last"},
        category="skills",
        entry_identity="s1",
    )
    assert result.from_store() is True
    assert result.single() == b"stored-tree-zip"
    assert result.content_type() == "application/zip"
    assert result.note() and "keep_last" in result.note()


def test_git_keep_last_has_no_receipt_to_reuse_after_a_re_pin(rig):
    """``keep_last`` reuses the stored copy of *what this pair last
    resolved to*. After a re-pin there is no such copy — the baseline under
    the old ``(url, ref)`` belongs to the ref the document just stopped
    naming — so the fetch failure stands rather than delivering the previous
    pin's bytes under the new one's name."""
    content, _, pipeline = rig
    old_sha = "b" * 40
    baseline_url = git_receipt_url(GIT_URL, old_sha, "pkg")
    content.store(
        fetched_object(b"stored-tree-zip", url=baseline_url,
                       content_type="application/zip"),
        scope=None, source_url=baseline_url,
    )
    git = _ScriptedGit(error=FetchFailedError("git fetch failed"))
    ctx = make_context(source_session=_session(
        git,
        sources={"app": {"protocol": "git", "url": GIT_URL, "ref": "v2",
                         "subpath": "pkg"}},
        # Recorded against the ref the document used to name.
        baselines={(GIT_URL, "v1", "non_strict"): old_sha},
    ))
    with pytest.raises(EntryFetchError, match="git fetch failed"):
        pipeline.resolve(
            ctx,
            entry={"from": "app", "on_fetch_failure": "keep_last"},
            category="skills",
            entry_identity="s1",
        )


def test_git_credentials_reach_the_transport_as_headers(rig):
    _, credentials, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(source_session=_session(git, sources={
        "app": {"protocol": "git", "url": GIT_URL, "ref": "main", "auth": "ci-token"},
    }))
    pipeline.resolve(ctx, entry={"from": "app"}, category="skills")
    assert credentials.binding_calls == ["ci-token"]
    assert git.headers == [{"X-Custom-Auth": "payload-of-ci-token"}]


def _git_delivery(pipeline, ctx, *, subpath="pkg", auth=None):
    """The delivery the git road hands back for a source named ``"app"``.

    Built by ``GitSourceFetcher`` rather than by hand, so the store it files
    through is the one the fetcher bound it to — which is the whole of what
    replaced the caller reaching for ``receipt_url`` and ``auth``.
    """
    return pipeline.resolve(
        ctx, entry={"from": "app"}, category="skills", entry_identity="s1"
    )


def _git_source_ctx(*, subpath="pkg", auth=None, **kwargs):
    source = {"protocol": "git", "url": GIT_URL, "ref": "main", "subpath": subpath}
    if auth is not None:
        source["auth"] = auth
    return make_context(
        source_session=_session(_ScriptedGit(), sources={"app": source}),
        **kwargs,
    )


def test_a_git_delivery_files_canonical_entry_bytes_with_the_store(rig):
    content, _, pipeline = rig
    ctx = _git_source_ctx(apply_id="apply-1")
    delivery = _git_delivery(pipeline, ctx)

    digest = delivery.file(
        ctx, b"canonical-zip",
        category="skills", entry_identity="s1",
        content_type="application/zip",
    )
    assert digest == "sha256:" + hashlib.sha256(b"canonical-zip").hexdigest()
    call = content.store_calls[-1]
    assert call["source_url"] == f"git+{GIT_URL}@{_FAKE_SHA}:pkg"
    assert call["apply_id"] == "apply-1"
    assert call["entry_identity"] == "s1"


class _Ledger:
    """A duck-typed ApplyFetchBudget that records what was charged."""

    def __init__(self) -> None:
        self.charged: list[int] = []

    def expired(self):
        return None

    def charge(self, size_bytes: int) -> None:
        self.charged.append(size_bytes)


def test_the_git_fetchs_declared_bytes_charge_the_apply_ledger_once(rig):
    _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = _with_budget(
        make_context(source_session=_session(git, sources={
            "app": {"protocol": "git", "url": GIT_URL, "ref": "main", "subpath": "pkg"},
        })),
        _Ledger(),
    )
    # Two entries name the same source: one fetch, one charge — a cached
    # checkout answers a read, not a fetch, the URL road's fast-path ruling.
    for name in ("first", "second"):
        pipeline.resolve(
            ctx, entry={"from": "app", "name": name}, category="skills",
            entry_identity=name,
        )
    assert ctx.budget.charged == [11]


def test_a_git_fetch_exhausting_the_byte_ledger_fails_the_entry(rig):
    _, _, pipeline = rig
    git = _ScriptedGit()
    from agentclaw.community.core.bot_config_manifest.apply.budget import (
        ApplyFetchBudget,
    )

    ctx = _with_budget(
        make_context(source_session=_session(git, sources={
            "app": {"protocol": "git", "url": GIT_URL, "ref": "main", "subpath": "pkg"},
        })),
        ApplyFetchBudget(deadline=9e99, total_bytes=5),
    )
    with pytest.raises(EntryFetchError, match="exhausted \\(bytes\\)"):
        pipeline.resolve(
            ctx, entry={"from": "app"}, category="skills"
        )


def test_entry_subpath_composes_with_the_sources_on_the_git_road(rig):
    """Defect D3, closed — and the precondition for ``resources`` over git.

    ``subpath`` is what distinguishes one entry from another, so refusing it
    beside a git source meant one source could serve exactly one entry: two
    files from one repository needed two source blocks carrying duplicate
    ``url``, ``ref`` and ``auth``, which is the drift named sources exist to
    remove. The two now compose, source's first.
    """
    _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(source_session=_session(git, sources={
        "app": {"protocol": "git", "url": GIT_URL, "ref": "main", "subpath": "pkg"},
    }))
    decl = pipeline.resolve(
        ctx, entry={"from": "app", "subpath": "narrow/inner.md"},
        category="skills",
    )
    assert isinstance(decl, GitDelivery)
    assert decl.source.subpath == "pkg/narrow/inner.md"
    assert git.specs[-1].subpath == "pkg/narrow/inner.md"


def test_a_source_with_no_subpath_takes_the_entrys_whole(rig):
    _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(source_session=_session(git, sources={
        "app": {"protocol": "git", "url": GIT_URL, "ref": "main"},
    }))
    decl = pipeline.resolve(
        ctx, entry={"from": "app", "subpath": "kb/faq.csv"}, category="skills"
    )
    assert decl.source.subpath == "kb/faq.csv"


def test_two_entries_off_one_git_source_share_a_checkout_and_a_sha(rig):
    """One source, two paths, one fetch, one ``resolved_sha``.

    The composition must not cost a second checkout: the cache keys on
    ``(url, ref)`` and the report row on the source's *display*, neither of
    which an entry's subpath changes. If it did, the report would carry two
    rows for one declared source.
    """
    _, _, pipeline = rig
    git = _ScriptedGit()
    session = _session(git, sources={
        "app": {"protocol": "git", "url": GIT_URL, "ref": "main", "subpath": "kb"},
    })
    ctx = make_context(source_session=session)
    first = pipeline.resolve(
        ctx, entry={"from": "app", "subpath": "faq.csv"}, category="resources_file"
    )
    second = pipeline.resolve(
        ctx, entry={"from": "app", "subpath": "pricing.csv"},
        category="resources_file",
    )
    assert (first.source.subpath, second.source.subpath) == ("kb/faq.csv", "kb/pricing.csv")
    assert len(git.specs) == 1, "one checkout per (url, ref) per apply"
    records = session.resolution_records()
    assert len(records) == 1
    assert records[0].name == "app"
    assert first.source.checkout.sha == second.source.checkout.sha == records[0].resolved_sha


def test_a_composed_subpath_that_escapes_the_tree_is_refused(rig):
    """Two individually safe halves can compose into a traversal, and the
    composed value is re-checked by the schema's own predicate — not by a
    second, weaker rule here, which is how one gets through by satisfying the
    other."""
    _, _, pipeline = rig
    ctx = make_context(source_session=_session(_ScriptedGit(), sources={
        "app": {"protocol": "git", "url": GIT_URL, "ref": "main", "subpath": "pkg"},
    }))
    with pytest.raises(EntryFetchError, match="'..' segment"):
        pipeline.resolve(
            ctx, entry={"from": "app", "subpath": "../../etc/shadow"},
            category="skills",
        )


def test_an_entry_subpath_is_not_part_of_an_object_stores_address(objects_rig):
    """One rule, both protocols: ``subpath`` selects within what the source
    delivered. Git delivers a tree, so it composes into the checkout's path;
    an object store delivers one object, so ``subpath`` is the materialiser's
    archive-internal selector and never part of the key the platform reads.
    ``key`` is what addresses the object, and it composes separately."""
    _, objects, pipeline = objects_rig
    objects.put("b", "pkg.tar.gz", BODY, access_key_id=_OBJ_AK)
    ctx = make_context(source_session=_session(_ScriptedGit(), sources={
        "cdn": {
            "protocol": "oss",
            "bucket": "b",
            "key": "pkg.tar.gz",
            "auth": "oss-cred",
        },
    }))
    pipeline.resolve(
        ctx,
        entry={"from": "cdn", "subpath": "inside/archive.md"},
        category="skills",
        entry_identity="qc",
    )
    assert objects.calls[0][1] == "pkg.tar.gz"  # subpath left out of it


def test_entry_level_auth_on_an_inline_git_source_is_refused(rig):
    _, _, pipeline = rig
    ctx = make_context(source_session=_session(_ScriptedGit()))
    with pytest.raises(EntryFetchError, match="'auth' is not supported on a git"):
        pipeline.resolve(
            ctx,
            entry={"source": {"protocol": "git", "url": GIT_URL, "ref": "main"}, "auth": "ci-token"},
            category="skills",
        )


def test_a_git_delivery_files_the_credential_name_on_git_receipts(rig):
    content, _, pipeline = rig
    ctx = _git_source_ctx(auth="ci-token", apply_id="apply-1")
    delivery = _git_delivery(pipeline, ctx)

    delivery.file(
        ctx, b"canonical-zip", category="skills", entry_identity="s1",
    )
    # The lineage answers "which named credential distributed this content"
    # identically on the object and git roads — and the caller no longer
    # threads the name, because the source the delivery came from carries it.
    assert content.store_calls[-1]["credential_name"] == "ci-token"


def test_the_object_road_files_once_and_its_delivery_adds_nothing(objects_rig):
    """The other half of the seam's filing rule, end to end.

    The fetch filed what it read on its way past, so the caller's
    unconditional ``file`` must not make a second receipt for one entry —
    ``keep_last`` would then read whichever it found first.
    """
    content, objects, pipeline = objects_rig
    objects.put("b", "k", BODY, access_key_id=_OBJ_AK)
    ctx = _oss_ctx()

    delivery = pipeline.resolve(
        ctx, entry={"from": "s"}, category="identity", entry_identity="soul"
    )
    assert len(content.store_calls) == 1

    digest = delivery.file(
        ctx, delivery.single(), category="identity", entry_identity="soul"
    )
    assert len(content.store_calls) == 1
    assert digest == DIGEST


def test_the_git_road_carries_the_auth_and_the_category_limit(rig):
    content, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(source_session=_session(git, sources={
        "app": {"protocol": "git", "url": GIT_URL, "ref": "main", "auth": "ci-token"},
    }))
    decl = pipeline.resolve(ctx, entry={"from": "app"}, category="identity")
    assert isinstance(decl, GitDelivery)
    # The identity category's per-entry cap rides the source: its reader
    # refuses a member by DECLARED size against the category number, the
    # same vocabulary the URL road's transport enforces.
    assert decl.source.file_limit == 1 * 1024 * 1024
    # The credential NAME rides the source, and it is what lands on the
    # receipt when the caller asks this delivery to file its bytes.
    decl.file(ctx, b"one-file", category="identity", entry_identity="x")
    assert content.store_calls[-1]["credential_name"] == "ci-token"


# --- the oss road carries a signing credential (defect D4) ------------------


def test_an_oss_source_without_auth_is_refused_before_any_read(objects_rig):
    """No anonymous road, and the refusal is at PUT as well as here.

    A bucket read needs an endpoint, and the endpoint is a property of the
    credential — with no ``auth`` there is nowhere to send the request.
    Accepting such a source and failing at apply would be the surface
    accepting what it cannot apply, so the schema refuses it too; this is the
    belt for a document that reached storage before that rule existed.
    """
    _, objects, pipeline = objects_rig
    ctx = make_context(source_session=_session(_ScriptedGit(), sources={
        "cdn": {"protocol": "oss", "bucket": "b", "key": "k"},
    }))
    with pytest.raises(EntryFetchError, match="auth"):
        pipeline.resolve(ctx, entry={"from": "cdn"}, category="identity")
    assert objects.calls == []  # refused before the store was touched




# --- 'source' is a declaration, and nothing else ----------------------------


def test_a_source_written_as_a_plain_url_string_is_refused(rig):
    """The supported set is exactly three roads — inline ``content``, a
    declared ``oss`` object, a declared ``git`` repository — and a ``source``
    naming a URL directly is none of them.

    The ``PUT`` validator has refused that spelling for some time; the apply
    pipeline kept a read-side branch for documents already stored under the
    old grammar, and there are none left. The refusal names the shape the
    author has to write instead, because the entry it is refusing was
    written by a person, not produced by the platform.
    """
    _, _, pipeline = rig
    ctx = make_context(source_session=_session(_ScriptedGit()))
    with pytest.raises(EntryFetchError) as raised:
        pipeline.resolve(
            ctx,
            entry={"source": "https://example.com/x.zip"},
            category="skills",
        )
    reason = raised.value.reason
    assert "declaration object" in reason and "protocol" in reason
    assert "protocol: git" in reason and "protocol: oss" in reason
    # Refused at the front door — and there is no longer a URL transport to
    # have asked in the first place: the entry point that dialled one is gone.
    assert not hasattr(pipeline, "_fetcher")


def test_declared_protocol_cannot_answer_for_a_source_that_is_not_a_declaration():
    """``declared_protocol`` answers from the declaration or not at all.

    It used to call a string ``source`` ``OSS``, which was the legacy road's
    classification. ``None`` — "cannot say from the declaration alone" — is
    what the callers already handle: they fall through to the fetch, which
    raises the real error with the real message rather than a rule derived
    from a shape nothing supports.
    """
    ctx = make_context()
    assert declared_protocol(ctx, {"source": "https://example.com/x.zip"}) is None
    assert declared_protocol(ctx, {"content": "inline text"}) is None
    assert (
        declared_protocol(
            ctx,
            {"source": {"protocol": "oss", "bucket": "b", "key": "k", "auth": "c"}},
        )
        is SourceKind.OSS
    )
