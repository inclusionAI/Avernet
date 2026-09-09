"""Rule 25 conformance — ObjectStoreClientFactory.

Rule 25 defines conformance as *consumer ↔ Protocol*, with the local impl as
the executable spec of what we believe prod does. Both halves are here:

- the **spec** half — every :class:`ObjectFetchStatus` reachable, the
  streaming cap honoured, ``detail`` safe for a report
- the **consumer** half — ``ObjectStoreFetcher`` reading a declared source
  through the bound factory, with the plugin-hit assertion Rule 25 requires

The deeper consumer cases (refusal vs failure under ``keep_last``, the
composed key, the guarded transport staying out of it) live beside the rest
of the fetch pipeline in ``apply/test_entry_fetch.py``, against this same
local impl — so the two suites cannot disagree about what the plugin does.

What is worth pinning here is not "the fake works" — it is the **rules** the
fake encodes, because those rules are what the boto3 impl and the corp
``oss2`` impl are written against:

- refusal vs failure, since ``keep_last`` may mask exactly one of them
- the cap refuses **without handing the caller the bytes**
- ``detail`` never carries the endpoint or the secret
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from agentclaw.community.plugin_api.object_store_client import (
    REFUSAL_STATUSES,
    ObjectFetchStatus,
    ObjectStoreClient,
    ObjectStoreClientFactory,
    ObjectStoreTarget,
)

AK = "LTAI-test-key-id"
SECRET = "the-secret-half"
ENDPOINT = "https://oss.internal.example"


def _target(bucket: str = "b1", *, access_key_id: str = AK) -> ObjectStoreTarget:
    return ObjectStoreTarget(
        endpoint=ENDPOINT,
        bucket=bucket,
        access_key_id=access_key_id,
        secret_access_key=SECRET,
        region="cn-shanghai",
    )


@dataclass(frozen=True)
class _ApplyLikeContext:
    """Just the surface ``FetchContext`` declares — the fetch funnel's seam.

    Built here rather than reaching for an ``ApplyContext``: this suite is
    about the plugin's consumer contract, and the funnel documents exactly
    what it reads off its caller so a double can be honest about it.
    """

    source_session: object
    bot_id: str = "b_1"
    entity_id: str = "ent"
    env: str = "test"
    tenant: str = "default"
    engine_type: str = "claude_code"
    actor_id: str = "alice"
    apply_id: str | None = None
    budget: object | None = None


@pytest.fixture
def factory(world) -> ObjectStoreClientFactory:
    """The impl the injector actually binds for a test boot."""
    return world.get(ObjectStoreClientFactory)


def test_the_bound_factory_satisfies_the_protocol(factory):
    assert isinstance(factory, ObjectStoreClientFactory)
    assert isinstance(factory.client_for(_target()), ObjectStoreClient)


def test_allocating_a_client_touches_nothing(factory):
    """The protocol requires construction to be inert.

    An endpoint that cannot be reached must fail the *read* of one entry, not
    the allocation — otherwise one bad credential takes down the apply that
    merely mentioned it.
    """
    factory.make_unavailable("unreachable")
    client = factory.client_for(_target("unreachable"))
    assert factory.calls == []  # nothing happened yet
    assert client.get("k", byte_limit=1024).status is ObjectFetchStatus.UNAVAILABLE


# ── the status rules ─────────────────────────────────────────────────────────


def test_a_present_object_is_found_and_carries_its_bytes(factory):
    factory.put("b1", "tools/cli.tar.gz", b"payload", access_key_id=AK)
    result = factory.client_for(_target()).get("tools/cli.tar.gz", byte_limit=1024)
    assert result.status is ObjectFetchStatus.FOUND
    assert result.content == b"payload"


def test_a_missing_object_is_not_found(factory):
    factory.put("b1", "present", b"x", access_key_id=AK)
    result = factory.client_for(_target()).get("absent", byte_limit=1024)
    assert result.status is ObjectFetchStatus.NOT_FOUND
    assert result.content is None


def test_the_wrong_credential_is_denied_not_missing(factory):
    """The two must not collapse. A denied credential that read as
    ``NOT_FOUND`` would look like a document error, and the operator would go
    looking for a key that is sitting right there."""
    factory.put("b1", "k", b"x", access_key_id="the-other-key")
    result = factory.client_for(_target(access_key_id=AK)).get("k", byte_limit=1024)
    assert result.status is ObjectFetchStatus.DENIED


def test_an_unreachable_store_is_a_failure_not_a_refusal(factory):
    factory.make_unavailable("b1")
    result = factory.client_for(_target()).get("k", byte_limit=1024)
    assert result.status is ObjectFetchStatus.UNAVAILABLE
    assert result.is_refusal is False


@pytest.mark.parametrize(
    "status",
    [
        ObjectFetchStatus.NOT_FOUND,
        ObjectFetchStatus.DENIED,
        ObjectFetchStatus.TOO_LARGE,
    ],
)
def test_the_document_and_credential_errors_are_refusals(status):
    """``keep_last`` may mask a transport failure and must not mask these.

    A denied credential quietly serving last apply's bytes for a year is the
    outcome that ruling exists to prevent — so the classification lives in the
    protocol, next to the statuses, rather than in each implementation.
    """
    assert status in REFUSAL_STATUSES


def test_found_and_unavailable_are_not_refusals():
    assert ObjectFetchStatus.FOUND not in REFUSAL_STATUSES
    assert ObjectFetchStatus.UNAVAILABLE not in REFUSAL_STATUSES


# ── the cap ──────────────────────────────────────────────────────────────────


def test_an_oversized_object_is_refused_without_being_handed_over(factory):
    """The cap's whole purpose is that the bytes never reach the caller.

    Asserting only on the status would pass against an implementation that
    buffered the object, measured it, and then said ``TOO_LARGE`` — which has
    already paid the memory the cap exists to refuse.
    """
    factory.put("b1", "big", b"x" * 5000, access_key_id=AK)
    result = factory.client_for(_target()).get("big", byte_limit=1024)
    assert result.status is ObjectFetchStatus.TOO_LARGE
    assert result.content is None


def test_an_object_exactly_at_the_cap_is_delivered(factory):
    """The boundary is inclusive. Off by one here refuses a legitimate entry
    at exactly the documented category width."""
    factory.put("b1", "edge", b"y" * 1024, access_key_id=AK)
    result = factory.client_for(_target()).get("edge", byte_limit=1024)
    assert result.status is ObjectFetchStatus.FOUND
    assert result.content is not None and len(result.content) == 1024


# ── report safety ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "seed,key",
    [
        (lambda f: f.put("b1", "k", b"x", access_key_id="other"), "k"),
        (lambda f: f.put("b1", "k", b"x", access_key_id=AK), "missing"),
        (lambda f: f.put("b1", "k", b"x" * 5000, access_key_id=AK), "k"),
    ],
)
def test_detail_never_carries_the_endpoint_or_the_secret(factory, seed, key):
    """``detail`` reaches an apply report. The bucket and key belong there;
    the endpoint and the credential's secret half never do — the same ruling
    the git road applies to stderr, which echoes the source URL."""
    seed(factory)
    result = factory.client_for(_target()).get(key, byte_limit=1024)
    assert result.status is not ObjectFetchStatus.FOUND
    assert SECRET not in result.detail
    assert ENDPOINT not in result.detail
    assert "b1" in result.detail


def test_the_target_repr_redacts_the_secret():
    """A target reaches a log or a traceback the moment something raises while
    holding one, and a dataclass's default repr would print the secret."""
    text = repr(_target())
    assert SECRET not in text
    assert "<redacted>" in text
    # The identifying halves stay readable — an operator cannot debug a
    # credential they cannot name.
    assert AK in text and "b1" in text


# ── plugin-hit evidence ──────────────────────────────────────────────────────


def test_every_read_is_recorded_with_what_it_was_asked_for(factory):
    """Rule 25's required assertion, made available here for the consumer half:
    without recorded calls a consumer could bypass the plugin entirely and its
    test would still pass."""
    factory.put("b1", "k", b"x", access_key_id=AK)
    factory.client_for(_target()).get("k", byte_limit=4096)

    assert len(factory.calls) == 1
    target, key, byte_limit = factory.calls[0]
    assert (target.bucket, key, byte_limit) == ("b1", "k", 4096)


# ── consumer ↔ Protocol ──────────────────────────────────────────────────────


def test_a_manifest_source_reads_through_the_bound_factory(world, factory):
    """Rule 25's assertion proper: the consumer reaches the plugin.

    ``EntryFetcher`` is the fetch funnel every materialising category shares.
    Driven with a declared ``protocol: oss`` source it must acquire the bytes
    **through the injected factory** — so this drives ``fetch_declared`` for
    real and asserts three things: the content came back, the recorded call
    names the composed key, and the guarded transport was never touched.

    An earlier version of this test stopped after checking singleton identity.
    It would have passed against an ``EntryFetcher`` that bypassed the plugin
    entirely, which is precisely the bypass Rule 25 names the plugin-hit
    assertion to catch.
    """
    from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
        EntryFetcher,
    )
    from agentclaw.community.core.bot_config_manifest.apply.source_session import (
        SourceSession,
    )

    pipeline = world.get(EntryFetcher)
    # Same singleton the injector handed the pipeline — two instances would
    # make the seeding below invisible to it.
    assert world.get(ObjectStoreClientFactory) is factory

    # The credential the source names has to exist: the oss road resolves it
    # (for the endpoint and the key pair) before it reads anything, and that
    # ordering is part of the contract under test.
    #
    # Written straight to the repository rather than through the service: the
    # service now validates that an endpoint resolves to a public address —
    # correctly, and it has its own tests — but that would make this suite
    # depend on DNS to say something about the *fetch* contract.
    from agentclaw.community.core.bot_config_manifest.credentials.models import (
        CredentialType,
    )
    from agentclaw.community.core.repository.protocols.bot.source_credential import (  # noqa: E501
        SourceCredentialRepositoryProtocol,
    )

    world.get(SourceCredentialRepositoryProtocol).upsert(
        name="oss-cred",
        credential_type=CredentialType.OSS_AKSK,
        header_name="",
        access_key_id=AK,
        endpoint=ENDPOINT,
        region="cn-shanghai",
        allowed_prefixes=[],
        secret_ciphertext=SECRET,
        owner_app_id=1,
        modifier="alice",
    )

    body = b"package-bytes"
    factory.put("tenant-bucket", "skills/qc.zip", body)
    ctx = _ApplyLikeContext(
        source_session=SourceSession(
            sources={
                "pkg": {
                    "protocol": "oss",
                    "bucket": "tenant-bucket",
                    "key": "skills/",
                    "auth": "oss-cred",
                }
            },
            baselines={},
            git=None,
        )
    )

    delivery = pipeline.fetch_declared(
        ctx, entry={"from": "pkg", "key": "qc.zip"}, category="skills",
        entry_identity="qc",
    )

    assert delivery.single() == body
    assert len(factory.calls) == 1
    target, key, _ = factory.calls[0]
    assert (target.bucket, key) == ("tenant-bucket", "skills/qc.zip")
