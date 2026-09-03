"""Tests for the per-entry fetch pipeline (``apply/entry_fetch.py``, W5).

The pipeline is where the three fetch-side waves meet for the first time: W2's
transport, W3's named credentials, W11's platform copy. What these tests pin
is the *policy* on top of them — pinned entries read from the store, unpinned
entries re-fetch, ``keep_last`` reads the receipt only when it may — and that
a secret cannot ride out through an error.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetchError,
    EntryFetcher,
    GitEntrySource,
)
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitSourceSpec,
    git_receipt_url,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
    FetchRefusedError,
)

from ._fakes import (
    FakeCredentials,
    FakeGuardedFetcher,
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


@pytest.fixture
def rig():
    content = FakeManifestContent()
    fetcher = FakeGuardedFetcher(responses={URL: fetched_object(BODY, url=URL)})
    credentials = FakeCredentials()
    return content, fetcher, credentials, EntryFetcher(fetcher, content, credentials)


def test_placeholders_substitute_before_the_transport_sees_the_url(rig):
    content, fetcher, _, pipeline = rig
    ctx = make_context()  # env="dev"

    substituted = "https://content.example/dev/payload.bin"
    fetcher.responses[substituted] = fetched_object(BODY, url=substituted)
    result = pipeline.fetch(
        ctx,
        source_url="https://content.example/${BOT_ENV}/payload.bin",
        category="identity",
    )
    # The wire saw the substituted URL: the fake answers only for it, and the
    # receipt is filed under it — substitution, then transport, then store.
    assert fetcher.requests[0].url == substituted
    assert result.content == BODY
    assert result.from_store is False
    assert content.store_calls[0]["source_url"] == substituted


def test_a_pinned_entry_with_a_matching_receipt_is_served_from_the_store(rig):
    content, fetcher, _, pipeline = rig
    _store_serving(content)

    result = pipeline.fetch(
        make_context(), source_url=URL, digest=DIGEST, category="identity"
    )
    # No network: the platform's own copy answers for the pinned bytes.
    assert fetcher.requests == []
    assert result.from_store is True
    assert result.content == BODY
    assert result.digest == DIGEST


def test_a_pinned_entry_with_a_mismatched_receipt_refetches(rig):
    content, fetcher, _, pipeline = rig
    _store_serving(content)
    # The source legitimately rotated: it now serves bytes pinned by a NEW
    # digest, so the platform's old receipt for this URL is stale, not "last".
    rotated = b"rotated-bytes"
    rotated_digest = "sha256:" + hashlib.sha256(rotated).hexdigest()
    fetcher.responses[URL] = fetched_object(rotated, url=URL)

    result = pipeline.fetch(
        make_context(), source_url=URL, digest=rotated_digest, category="identity"
    )
    assert len(fetcher.requests) == 1
    assert fetcher.requests[0].expected_digest == rotated_digest
    assert fetcher.requests[0].injector is None
    assert result.from_store is False
    assert result.digest == rotated_digest
    assert len(content.store_calls) == 1


def test_an_unpinned_entry_refetches_even_when_a_receipt_exists(rig):
    content, fetcher, credentials, pipeline = rig
    _store_serving(content)

    result = pipeline.fetch(make_context(), source_url=URL, category="identity")
    # No pin means "whatever is there now": the source is re-read so an apply
    # converges to it, never to our own memory of it.
    assert len(fetcher.requests) == 1
    assert result.from_store is False


def test_a_pinned_entry_with_a_matching_receipt_survives_a_dead_source(rig):
    """A pinned entry with a matching receipt never reaches the network at
    all — so a source that is DOWN between applies costs nothing: content
    addressing makes the stored bytes *the* declared bytes regardless of
    availability. This is the store-hit fast path, NOT a keep_last fallback
    (no fetch was attempted, none failed), and it carries no note: silence
    here is legitimate — the pinned fast path is exactly what convergence
    looks like, while a keep_last fallback is what "the source failed" looks
    like and must be reported. The two are distinguished by the mark."""
    content, fetcher, _, pipeline = rig
    _store_serving(content)
    # The source is unreachable — and it will never be asked.
    failing = FakeGuardedFetcher(failures={URL: FetchFailedError("source transport failed")})
    pipeline_failing = EntryFetcher(failing, content, FakeCredentials())

    result = pipeline_failing.fetch(
        make_context(),
        source_url=URL,
        digest=DIGEST,
        category="identity",
        keep_last=True,
    )
    assert result.from_store is True
    assert result.content == BODY
    assert result.digest == DIGEST
    assert failing.requests == []  # never reached the dead source
    assert result.fallback_reason is None  # a fast path, not a fallback


def test_keep_last_with_no_receipt_fails(rig):
    _, fetcher, _, _ = rig
    failing = FakeGuardedFetcher(failures={URL: FetchFailedError("source answered 404")})
    pipeline = EntryFetcher(failing, FakeManifestContent(), FakeCredentials())

    with pytest.raises(EntryFetchError) as excinfo:
        pipeline.fetch(
            make_context(),
            source_url=URL,
            category="identity",
            keep_last=True,
        )
    # The transport's own words — W2 already refuses before sending anything
    # that would carry the caller's or the source's data.
    assert "source answered 404" in excinfo.value.reason


def test_keep_last_with_an_unpinned_entry_falls_back_to_the_last_digest(rig):
    """An unpinned keep_last entry reuses the last-fetched bytes — that is the
    whole of "keep_last" for a declaration that pinned nothing."""
    content, _, _, _ = rig
    _store_serving(content)
    failing = FakeGuardedFetcher(failures={URL: FetchFailedError("source transport failed")})
    pipeline = EntryFetcher(failing, content, FakeCredentials())

    result = pipeline.fetch(
        make_context(), source_url=URL, category="identity", keep_last=True
    )
    assert result.from_store is True
    assert result.digest == DIGEST
    # Marked here too: the mode is on the entry, not on the claim of a pin.
    assert result.fallback_reason is not None


def test_keep_last_never_supplies_bytes_that_disagree_with_a_pin(rig):
    content, fetcher, _, pipeline = rig
    _store_serving(content)
    other = "sha256:" + "1" * 64
    fetcher.responses = {URL: fetched_object(b"", url=URL)}
    fetcher.failures = {URL: FetchFailedError("source transport failed")}

    with pytest.raises(EntryFetchError) as excinfo:
        pipeline.fetch(
            make_context(),
            source_url=URL,
            digest=other,
            category="identity",
            keep_last=True,
        )
    # The receipt is stale, not "last": refused rather than silently pinning
    # bytes the declaration never named.
    assert "source transport failed" in excinfo.value.reason


def test_a_fetch_failure_without_keep_last_fails_the_entry(rig):
    content, _, credentials, _ = rig
    failing = FakeGuardedFetcher(failures={URL: FetchFailedError("source answered 503")})
    pipeline = EntryFetcher(failing, content, credentials)

    with pytest.raises(EntryFetchError) as excinfo:
        pipeline.fetch(make_context(), source_url=URL, category="identity")
    assert "source answered 503" in excinfo.value.reason


def test_neither_the_error_nor_the_store_carries_a_credential_value(rig):
    """The secrecy seam is W2's and W3's own error texts; this pipeline's job
    is not to weaken them. The transport's message passes through verbatim —
    never the headers, never the binding — and a failed fetch stores nothing
    with even the credential *name* attached (no store event happened)."""
    content, _, credentials, pipeline = rig
    failing = FakeGuardedFetcher(
        failures={URL: FetchFailedError("source answered 401")}
    )
    pipeline_failing = EntryFetcher(failing, content, credentials)

    with pytest.raises(EntryFetchError) as excinfo:
        pipeline_failing.fetch(
            make_context(),
            source_url=URL,
            auth="mirror",
            category="identity",
        )
    assert excinfo.value.reason == "source answered 401"
    # The binding travelled as the request's headers/policy, never as message
    # or log-line text; and no receipt was filed for a failed acquisition.
    assert failing.requests[0].injector is not None
    assert "mirror" not in excinfo.value.reason
    assert content.store_calls == []


def test_the_credential_binding_reaches_the_transport_by_name(rig):
    content, fetcher, credentials, pipeline = rig

    result = pipeline.fetch(
        make_context(),
        source_url=URL,
        category="identity",
        auth="mirror",
    )
    # The binding is injector AND policy — one object, both seams, the way
    # SourceCredentialBinding satisfies both.
    binding = fetcher.requests[0].injector
    assert binding is fetcher.requests[0].policy
    assert binding.headers_for(None) == {"X-Custom-Auth": "payload-of-mirror"}
    assert credentials.binding_calls == ["mirror"]
    assert content.store_calls[0]["credential_name"] == "mirror"
    assert result.content == BODY


def test_a_missing_credential_fails_with_the_name_and_no_binding(rig):
    content, _, _, _ = rig
    fetcher = FakeGuardedFetcher(responses={URL: fetched_object(BODY, url=URL)})
    pipeline = EntryFetcher(
        fetcher, content, FakeCredentials(missing={"ghost"})
    )

    with pytest.raises(EntryFetchError) as excinfo:
        pipeline.fetch(
            make_context(), source_url=URL, category="identity", auth="ghost"
        )
    assert "ghost" in excinfo.value.reason
    assert fetcher.requests == []


def test_the_store_receives_the_actor_as_modifier(rig):
    content, _, _, pipeline = rig
    pipeline.fetch(make_context(), source_url=URL, category="identity")
    assert content.store_calls[0]["modifier"] == "u_actor"


def test_a_prefix_escape_refusal_becomes_the_same_entry_error(rig):
    """W3's policy refuses per hop — the substituted URL or a redirect
    stepping outside the credential's prefixes. That refusal reaches the
    materialiser as the entry's own failure, reason intact: the entry fails
    like a refused address, with the credential *named* (never valued)."""
    _, _, _, _ = rig
    from agentclaw.community.core.bot_config_manifest.credentials.policy import (
        PrefixAuthorizationError,
    )

    refusing = FakeGuardedFetcher(
        failures={
            URL: PrefixAuthorizationError(
                "credential 'mirror' is not authorized to leave "
                "https://mirror.example/prefix"
            )
        }
    )
    pipeline = EntryFetcher(refusing, FakeManifestContent(), FakeCredentials())

    with pytest.raises(EntryFetchError) as excinfo:
        pipeline.fetch(
            make_context(),
            source_url=URL,
            auth="mirror",
            category="identity",
        )
    assert "not authorized" in excinfo.value.reason
    assert "mirror" in excinfo.value.reason


def test_a_refused_transport_becomes_the_same_entry_error(rig):
    content, _, credentials, _ = rig
    refused = FakeGuardedFetcher(
        failures={URL: FetchRefusedError("non-public address for host")}
    )
    pipeline = EntryFetcher(refused, content, credentials)

    with pytest.raises(EntryFetchError) as excinfo:
        pipeline.fetch(make_context(), source_url=URL, category="identity")
    assert "non-public address" in excinfo.value.reason


# --- the P0-2 translation family: store faults are the ENTRY's failures ---


def test_a_pinned_blob_that_went_missing_heals_via_the_network(rig):
    """A pinned store-hit whose blob is gone (the store lost the file) is a
    self-repairing cache miss, not a caller-visible failure: the pin is
    byte-provable, the guarded fetch reacquires exactly those bytes, and the
    re-file heals the address. The result reads as an ordinary (network)
    fetch — from_store False, no fallback note: nothing FAILED."""
    content, fetcher, _, pipeline = rig
    _store_serving(content)
    content.missing_blobs.add(DIGEST)

    result = pipeline.fetch(
        make_context(), source_url=URL, digest=DIGEST, category="identity"
    )
    assert fetcher.requests, "the missing blob must be reacquired"
    assert result.content == BODY
    assert result.digest == DIGEST
    assert result.from_store is False
    assert result.fallback_reason is None
    # …and the address healed: the blob is readable again.
    assert content.read(DIGEST) == BODY


def test_a_pinned_blob_that_is_corrupt_loudly_fails_the_entry(rig):
    """A blob that exists but fails its own digest is disk-side damage — a
    hit a re-fetch CANNOT heal (the dedup write skips same-size files) — so
    it stays the 500-family failure it is, on this entry, with its reason;
    never a silent skip and never a wrapped whole-category abort."""
    content, _, _, pipeline = rig
    _store_serving(content)
    content.corrupt_blobs.add(DIGEST)

    with pytest.raises(EntryFetchError) as excinfo:
        pipeline.fetch(
            make_context(), source_url=URL, digest=DIGEST, category="identity"
        )
    assert "could not be read" in excinfo.value.reason
    assert "fails its own digest" in excinfo.value.reason


def test_a_store_side_refusal_of_the_lookup_is_the_entrys_error(rig):
    content, _, _, pipeline = rig
    from agentclaw.community.core.bot_config_manifest.content.errors import (
        ContentStoreError,
    )

    content.lookup_fault = ContentStoreError(
        "provenance fetched_url exceeds the 2048-char column: length 2200"
    )
    with pytest.raises(EntryFetchError) as excinfo:
        pipeline.fetch(
            make_context(), source_url=URL, digest="sha256:" + "0" * 64,
            category="identity",
        )
    # The store's own message (never the URL), as this entry's failure.
    assert "2048-char column" in excinfo.value.reason


def test_a_store_side_refusal_of_the_filing_is_the_entrys_error(rig):
    """The reachable shape the audit named: a redirect destination whose
    sanitized form exceeds the column — admission cannot see a redirect's
    Location, so the refusal lands here, AFTER the bytes were fetched. It
    fails ONE entry with the store's words, not the whole category under a
    wrapped 'resolve failed' surprise."""
    content, _, _, pipeline = rig
    from agentclaw.community.core.bot_config_manifest.content.errors import (
        ContentStoreError,
    )

    content.store_fault = ContentStoreError(
        "provenance fetched_url exceeds the 2048-char column: length 2200"
    )
    with pytest.raises(EntryFetchError) as excinfo:
        pipeline.fetch(make_context(), source_url=URL, category="identity")
    assert "could not be filed" in excinfo.value.reason
    assert "2048-char column" in excinfo.value.reason


def test_a_refusal_never_triggers_keep_last_even_when_a_receipt_exists(rig):
    """The ruling, pinned: a refused fetch (non-public address, scheme,
    hop budget, digest vocabulary) is a statement about the document's
    configuration — it never left the wire. Falling back to stored bytes
    would answer SUCCEEDED to a document the platform just refused, so the
    refusal fails the entry even with keep_last declared and a receipt in
    hand."""
    content, _, credentials, _ = rig
    _store_serving(content)
    refusing = FakeGuardedFetcher(
        failures={URL: FetchRefusedError("non-public address for host")}
    )
    pipeline = EntryFetcher(refusing, content, credentials)

    with pytest.raises(EntryFetchError) as excinfo:
        pipeline.fetch(
            make_context(),
            source_url=URL,
            digest=None,
            category="identity",
            keep_last=True,
        )
    assert "non-public address" in excinfo.value.reason
    # Nothing was served: the fallback did not fire.


def test_a_keep_last_read_failure_names_both_halves(rig):
    """The source failed, the fallback copy ALSO could not be read: the
    entry's error carries both reasons — drop either and the caller fixes
    the wrong thing (the audit called this swallowing)."""
    content, _, credentials, _ = rig
    _store_serving(content)
    content.missing_blobs.add(DIGEST)
    failing = FakeGuardedFetcher(
        failures={URL: FetchFailedError("source transport failed")}
    )
    pipeline = EntryFetcher(failing, content, credentials)

    with pytest.raises(EntryFetchError) as excinfo:
        pipeline.fetch(
            make_context(),
            source_url=URL,
            digest=DIGEST,
            category="identity",
            keep_last=True,
        )
    assert "source transport failed" in excinfo.value.reason
    assert "keep_last fallback copy could not be read" in excinfo.value.reason


def test_a_time_exhausted_budget_refuses_before_touching_the_network(rig):
    """The deadline is checked before the fetch — the audit's scenario was
    entries-long fetching that outran the apply-lock TTL and let the reaper
    hand a live apply's lock to a second one, so an exhausted budget must
    end the apply in bounded time with a named reason."""
    content, fetcher, credentials, pipeline = rig
    from agentclaw.community.core.bot_config_manifest.apply.budget import (
        ApplyFetchBudget,
    )

    ctx = make_context()
    ctx = _with_budget(ctx, ApplyFetchBudget(deadline=0.0, total_bytes=10**9))

    with pytest.raises(EntryFetchError) as excinfo:
        EntryFetcher(fetcher, content, credentials).fetch(
            ctx, source_url=URL, digest=None, category="identity"
        )
    assert "exhausted (time)" in excinfo.value.reason
    assert fetcher.requests == []


def test_a_byte_exhausted_budget_refuses_the_next_entry(rig):
    """Bytes charge per network fetch; the cap stops the N+1st entry, not
    the first — per-entry caps stay the fetcher's own business."""
    content, fetcher, credentials, _ = rig
    from agentclaw.community.core.bot_config_manifest.apply.budget import (
        ApplyFetchBudget,
    )

    budget = ApplyFetchBudget(deadline=1e18, total_bytes=len(BODY), clock=lambda: 0.0)
    ctx = _with_budget(make_context(), budget)
    pipeline = EntryFetcher(fetcher, content, credentials)

    first = pipeline.fetch(ctx, source_url=URL, digest=None, category="identity")
    assert first.content == BODY  # the first fetch fits exactly

    with pytest.raises(EntryFetchError) as excinfo:
        pipeline.fetch(ctx, source_url=URL, digest=None, category="identity")
    assert "exhausted (bytes)" in excinfo.value.reason
    # Only the FIRST fetch reached the wire; the refused one never did —
    # and note the first fetch's receipt does NOT serve the second (the
    # receipt exists, but an unpinned entry's re-fetch is the point).
    assert len(fetcher.requests) == 1


def test_the_funnel_requires_a_category_by_keyword(rig):
    """Linkage by default is linkage by accident: a call site that forgets
    its category would file an unattributed receipt and take the default
    cap. Reject the omission loudly (type analysis flagged this as one of
    the PR's two blocking-level type issues)."""
    import inspect

    from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
        EntryFetcher,
    )

    signature = inspect.signature(EntryFetcher.fetch)
    category = signature.parameters["category"]
    assert category.kind is inspect.Parameter.KEYWORD_ONLY
    assert category.default is inspect.Parameter.empty


# --- the W7 declared-source front door: fetch_declared / file_bytes ---

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


def test_fetch_declared_serves_a_url_from_a_named_source(rig):
    content, fetcher, credentials, pipeline = rig
    fetcher.responses["https://content.example/named.bin"] = fetched_object(
        BODY, url="https://content.example/named.bin"
    )
    ctx = make_context(
        source_session=_session(_ScriptedGit(), sources={
            "cdn": {"url": "https://content.example/named.bin", "auth": None},
        })
    )
    result = pipeline.fetch_declared(
        ctx, entry={"from": "cdn"}, category="identity"
    )
    # The same URL road as 'source', with the source's own auth folded in.
    assert result.content == BODY
    assert fetcher.requests[0].url == "https://content.example/named.bin"


def test_fetch_declared_gives_the_git_road_a_checkout(rig):
    _, _, credentials, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(
        source_session=_session(git, sources={
            "app": {"git": GIT_URL, "ref": "main", "subpath": "pkg"},
        })
    )
    decl = pipeline.fetch_declared(
        ctx, entry={"from": "app"}, category="skills", entry_identity="s1"
    )
    assert isinstance(decl, GitEntrySource)
    assert decl.checkout.sha == _FAKE_SHA
    assert decl.files() == [("skill.md", b"file-bytes")]
    # No named credential on this source: none was asked of W3, and the
    # session recorded the resolution the report will carry.
    assert credentials.binding_calls == []
    assert ctx.source_session.resolution_records()[0].resolved_sha == _FAKE_SHA


def test_fetch_declared_refuses_a_from_that_names_nothing(rig):
    _, _, _, pipeline = rig
    ctx = make_context(source_session=_session(_ScriptedGit()))
    with pytest.raises(EntryFetchError, match="not declared"):
        pipeline.fetch_declared(ctx, entry={"from": "ghost"}, category="skills")


def test_fetch_declared_missing_session_is_loud(rig):
    _, _, _, pipeline = rig
    ctx = make_context()
    with pytest.raises(EntryFetchError, match="no source session"):
        pipeline.fetch_declared(ctx, entry={"from": "x"}, category="skills")


def test_strict_refuses_when_the_ref_moved(rig):
    _, _, _, pipeline = rig
    git = _ScriptedGit()
    # An inline source's report identity is its repository URL, so that is
    # the key its baseline is read back by.
    ctx = make_context(
        source_session=_session(git, baselines={GIT_URL: "b" * 40})
    )
    with pytest.raises(EntryFetchError, match="moved"):
        pipeline.fetch_declared(
            ctx,
            entry={"source": {"git": GIT_URL, "ref": "main", "mode": "strict"}},
            category="skills",
        )
    # The refused move was NOT adopted: this apply's report records no
    # resolution for the source, so the next apply's baseline is still the
    # one this refusal was checked against — strict mode refuses every
    # apply until the document re-pins, not just the first.
    assert ctx.source_session.resolution_records() == ()


def test_non_strict_records_the_move_in_the_note(rig):
    _, _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(
        source_session=_session(git, baselines={GIT_URL: "b" * 40})
    )
    decl = pipeline.fetch_declared(
        ctx,
        entry={"source": {"git": GIT_URL, "ref": "main"}},
        category="skills",
    )
    assert isinstance(decl, GitEntrySource)
    assert decl.moved_note() and "b" * 40 in decl.moved_note()
    assert "a" * 40 in decl.moved_note()


def test_strict_on_the_first_apply_has_no_opinion(rig):
    _, _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(source_session=_session(git))  # no baselines
    decl = pipeline.fetch_declared(
        ctx,
        entry={"source": {"git": GIT_URL, "ref": "main", "mode": "strict"}},
        category="skills",
    )
    assert isinstance(decl, GitEntrySource)
    assert decl.moved_note() is None


def test_digest_on_a_git_source_is_refused(rig):
    _, _, _, pipeline = rig
    ctx = make_context(source_session=_session(_ScriptedGit()))
    with pytest.raises(EntryFetchError, match="digest"):
        pipeline.fetch_declared(
            ctx,
            entry={"source": {"git": GIT_URL, "ref": "main"},
                   "digest": "sha256:" + "0" * 64},
            category="skills",
        )


def test_git_keep_last_falls_back_to_the_baseline_receipt(rig):
    content, _, credentials, pipeline = rig
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
        sources={"app": {"git": GIT_URL, "ref": "main", "subpath": "pkg"}},
        baselines={"app": old_sha},
    ))
    result = pipeline.fetch_declared(
        ctx,
        entry={"from": "app", "on_fetch_failure": "keep_last"},
        category="skills",
        entry_identity="s1",
    )
    assert result.from_store is True
    assert result.content == b"stored-tree-zip"
    assert result.content_type == "application/zip"
    assert result.fallback_reason and "keep_last" in result.fallback_reason


def test_git_credentials_reach_the_transport_as_headers(rig):
    _, fetcher, credentials, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(source_session=_session(git, sources={
        "app": {"git": GIT_URL, "ref": "main", "auth": "ci-token"},
    }))
    pipeline.fetch_declared(ctx, entry={"from": "app"}, category="skills")
    assert credentials.binding_calls == ["ci-token"]
    assert git.headers == [{"X-Custom-Auth": "payload-of-ci-token"}]


def test_file_bytes_files_canonical_entry_bytes_with_the_store(rig):
    content, _, _, pipeline = rig
    ctx = make_context(apply_id="apply-1")
    digest = pipeline.file_bytes(
        ctx, content=b"canonical-zip",
        source_url=git_receipt_url(GIT_URL, _FAKE_SHA, "pkg"),
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
    _, _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = _with_budget(
        make_context(source_session=_session(git, sources={
            "app": {"git": GIT_URL, "ref": "main", "subpath": "pkg"},
        })),
        _Ledger(),
    )
    # Two entries name the same source: one fetch, one charge — a cached
    # checkout answers a read, not a fetch, the URL road's fast-path ruling.
    for name in ("first", "second"):
        pipeline.fetch_declared(
            ctx, entry={"from": "app", "name": name}, category="skills",
            entry_identity=name,
        )
    assert ctx.budget.charged == [11]


def test_a_git_fetch_exhausting_the_byte_ledger_fails_the_entry(rig):
    _, _, _, pipeline = rig
    git = _ScriptedGit()
    from agentclaw.community.core.bot_config_manifest.apply.budget import (
        ApplyFetchBudget,
    )

    ctx = _with_budget(
        make_context(source_session=_session(git, sources={
            "app": {"git": GIT_URL, "ref": "main", "subpath": "pkg"},
        })),
        ApplyFetchBudget(deadline=9e99, total_bytes=5),
    )
    with pytest.raises(EntryFetchError, match="exhausted \\(bytes\\)"):
        pipeline.fetch_declared(
            ctx, entry={"from": "app"}, category="skills"
        )


def test_entry_level_subpath_on_a_git_source_is_refused(rig):
    _, _, _, pipeline = rig
    ctx = make_context(source_session=_session(_ScriptedGit(), sources={
        "app": {"git": GIT_URL, "ref": "main", "subpath": "pkg"},
    }))
    # Entry-level 'subpath' is real vocabulary on the URL roads, so a caller
    # who writes it beside a git source believes they scoped something they
    # did not — the refusal says where scoping belongs for this form.
    with pytest.raises(EntryFetchError, match="'subpath' is not supported on a git"):
        pipeline.fetch_declared(
            ctx, entry={"from": "app", "subpath": "pkg/narrow"}, category="skills"
        )


def test_entry_level_auth_on_an_inline_git_source_is_refused(rig):
    _, _, _, pipeline = rig
    ctx = make_context(source_session=_session(_ScriptedGit()))
    with pytest.raises(EntryFetchError, match="'auth' is not supported on a git"):
        pipeline.fetch_declared(
            ctx,
            entry={"source": {"git": GIT_URL, "ref": "main"}, "auth": "ci-token"},
            category="skills",
        )


def test_file_bytes_files_the_credential_name_on_git_receipts(rig):
    content, _, _, pipeline = rig
    ctx = make_context(apply_id="apply-1")
    pipeline.file_bytes(
        ctx, content=b"canonical-zip",
        source_url=git_receipt_url(GIT_URL, _FAKE_SHA, "pkg"),
        category="skills", entry_identity="s1", credential_name="ci-token",
    )
    # The lineage answers "which named credential distributed this content"
    # identically on the URL and git roads.
    assert content.store_calls[-1]["credential_name"] == "ci-token"


def test_the_git_road_carries_the_auth_and_the_category_limit(rig):
    _, _, _, pipeline = rig
    git = _ScriptedGit()
    ctx = make_context(source_session=_session(git, sources={
        "app": {"git": GIT_URL, "ref": "main", "auth": "ci-token"},
    }))
    decl = pipeline.fetch_declared(ctx, entry={"from": "app"}, category="identity")
    assert isinstance(decl, GitEntrySource)
    # The identity category's per-entry cap rides the source: its reader
    # refuses a member by DECLARED size against the category number, the
    # same vocabulary the URL road's transport enforces.
    assert decl.file_limit == 1 * 1024 * 1024
    assert decl.auth == "ci-token"
