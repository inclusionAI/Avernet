"""Tests for the per-entry fetch pipeline (``apply/entry_fetch.py``, W5).

The pipeline is where the three fetch-side waves meet for the first time: W2's
transport, W3's named credentials, W11's platform copy. What these tests pin
is the *policy* on top of them — pinned entries read from the store, unpinned
entries re-fetch, ``keep_last`` reads the receipt only when it may — and that
a secret cannot ride out through an error.
"""
from __future__ import annotations

import hashlib

import pytest

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetchError,
    EntryFetcher,
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
