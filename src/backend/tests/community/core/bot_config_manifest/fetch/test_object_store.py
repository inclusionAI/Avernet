"""The ``oss`` road's client — ``AliyunObjectStore`` and the types it answers in.

Three things are pinned here, and they are the rules the apply layer leans
on rather than "the SDK works":

- **refusal vs failure** — ``keep_last`` may mask exactly one of them, so the
  classification of every SDK outcome is what keeps a denied credential from
  quietly serving last apply's bytes for a year;
- **the cap refuses while streaming**, without handing the caller the bytes;
- **``detail`` is report-safe** — bucket, key, verdict and the store's error
  code, never the SDK message that echoes the endpoint and signed material.

The SDK is exercised through the class's one seam, ``_open``: a scripted
bucket whose ``get_object`` raises a synthesised ``oss2`` exception or serves
a scripted stream. The exceptions are the SDK's own classes, built the way
``oss2.exceptions.make_exception`` builds them from a response, so the
``isinstance`` and status/code rules run against the real hierarchy. Two
cases use the real opener with no network in reach — the client-side errors
the SDK raises before any socket is opened.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import oss2.exceptions as oss_exc
import pytest
from requests import ConnectionError as RequestsConnectionError

from agentclaw.community.core.bot_config_manifest.fetch import object_store
from agentclaw.community.core.bot_config_manifest.fetch.object_store import (
    REFUSAL_STATUSES,
    AliyunObjectStore,
    ObjectFetchStatus,
    ObjectStoreTarget,
)

AK = "LTAI5tTestKeyId"
SECRET = "the-secret-half"
ENDPOINT = "https://oss-internal.example"
#: What the SDK's own message carries and ``detail`` never may: the endpoint,
#: and on some paths a signed query string with the key pair in it.
SDK_MESSAGE = (
    f"request to {ENDPOINT}/bkt/k?Signature={SECRET} was refused, "
    f"AccessKeyId={AK}"
)
WHERE = "object 'tools/qc.tgz' in bucket 'team-artifacts': "


def _target(bucket: str = "team-artifacts", *, region: str = "cn-shanghai") -> ObjectStoreTarget:
    return ObjectStoreTarget(
        endpoint=ENDPOINT,
        bucket=bucket,
        access_key_id=AK,
        secret_access_key=SECRET,
        region=region,
    )


def _server_error(klass, status: int, code: str, **extra):
    """An SDK error the way ``make_exception`` builds one from a response."""
    details = {"Code": code, "Message": SDK_MESSAGE, **extra}
    return klass(status, {"x-oss-request-id": "req-1"}, b"<Error/>", details)


# ── scripted SDK surface ─────────────────────────────────────────────────────


@dataclass
class _Stream:
    """A ``GetObjectResult`` double: serves ``body`` in reads of at most
    ``serve`` bytes, whatever the caller asks for, and counts everything."""

    body: bytes
    serve: int = 1 << 20
    fail_after: int | None = None
    close_error: BaseException | None = None
    reads: int = 0
    served: int = 0
    closed: bool = False

    def read(self, amt: int | None = None) -> bytes:
        self.reads += 1
        if self.fail_after is not None and self.served >= self.fail_after:
            raise ConnectionResetError("peer reset mid-body")
        take = min(len(self.body) - self.served, self.serve if amt is None else min(amt, self.serve))
        chunk = self.body[self.served : self.served + take]
        self.served += take
        return chunk

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


@dataclass
class _Bucket:
    """An ``oss2.Bucket`` double: one scripted answer per key."""

    outcomes: dict[str, object] = field(default_factory=dict)
    requested: list[str] = field(default_factory=list)

    def get_object(self, key: str):
        self.requested.append(key)
        outcome = self.outcomes[key]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@dataclass
class _Opener:
    bucket: _Bucket
    opened: list[ObjectStoreTarget] = field(default_factory=list)

    def __call__(self, target: ObjectStoreTarget) -> _Bucket:
        self.opened.append(target)
        return self.bucket


def _store(**outcomes) -> tuple[AliyunObjectStore, _Opener]:
    opener = _Opener(_Bucket(dict(outcomes)))
    return AliyunObjectStore(_open=opener), opener


# ── the status rules ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status",
    [ObjectFetchStatus.NOT_FOUND, ObjectFetchStatus.DENIED, ObjectFetchStatus.TOO_LARGE],
)
def test_the_document_and_credential_errors_are_refusals(status):
    """``keep_last`` may mask a transport failure and must not mask these.

    A denied credential quietly serving last apply's bytes for a year is the
    outcome that ruling exists to prevent — so the classification lives next
    to the statuses, not in the consumer."""
    assert status in REFUSAL_STATUSES


def test_found_and_unavailable_are_not_refusals():
    assert ObjectFetchStatus.FOUND not in REFUSAL_STATUSES
    assert ObjectFetchStatus.UNAVAILABLE not in REFUSAL_STATUSES


def test_the_target_repr_redacts_the_secret():
    """A target reaches a log or a traceback the moment something raises while
    holding one, and a dataclass's default repr would print the secret."""
    text = repr(_target())
    assert SECRET not in text
    assert "<redacted>" in text
    # The identifying halves stay readable — an operator cannot debug a
    # credential they cannot name.
    assert AK in text and "team-artifacts" in text and "cn-shanghai" in text


# ── _classify: every SDK outcome to a status and a sentence ─────────────────


@pytest.mark.parametrize(
    "exc,status,sentence",
    [
        pytest.param(
            _server_error(oss_exc.NoSuchKey, 404, "NoSuchKey"),
            ObjectFetchStatus.NOT_FOUND,
            "the object was not found (NoSuchKey)",
            id="NoSuchKey",
        ),
        pytest.param(
            _server_error(oss_exc.NoSuchBucket, 404, "NoSuchBucket"),
            ObjectFetchStatus.NOT_FOUND,
            "the object was not found (NoSuchBucket)",
            id="NoSuchBucket",
        ),
        pytest.param(
            _server_error(oss_exc.AccessDenied, 403, "AccessDenied"),
            ObjectFetchStatus.DENIED,
            "the credential was denied (AccessDenied)",
            id="AccessDenied",
        ),
        pytest.param(
            _server_error(oss_exc.SignatureDoesNotMatch, 403, "SignatureDoesNotMatch"),
            ObjectFetchStatus.DENIED,
            "the credential was denied (SignatureDoesNotMatch)",
            id="SignatureDoesNotMatch",
        ),
        pytest.param(
            # The SDK has no class for this pair; ``make_exception`` answers a
            # bare ``ServerError`` and only the status and code identify it.
            _server_error(oss_exc.ServerError, 403, "InvalidAccessKeyId"),
            ObjectFetchStatus.DENIED,
            "the credential was denied (InvalidAccessKeyId)",
            id="bare-403-InvalidAccessKeyId",
        ),
        pytest.param(
            # Clock skew is a refusal: the credential cannot be presented
            # from this host until the clock is fixed, and no retry changes
            # that.
            _server_error(oss_exc.ServerError, 403, "RequestTimeTooSkewed"),
            ObjectFetchStatus.DENIED,
            "the credential was denied (RequestTimeTooSkewed)",
            id="RequestTimeTooSkewed",
        ),
        pytest.param(
            _server_error(oss_exc.ServerError, 401, ""),
            ObjectFetchStatus.DENIED,
            "the credential was denied",
            id="bare-401",
        ),
        pytest.param(
            # The live-testing failure: an S3-style signature against the
            # native endpoint. The code is the diagnosis and MUST reach the
            # report — the corp version dropped it, and the one word that
            # identified the bug never left the log.
            _server_error(
                oss_exc.InvalidArgument, 400, "InvalidArgument", ArgumentName="Authorization"
            ),
            ObjectFetchStatus.NOT_FOUND,
            "the object store refused the request (InvalidArgument)",
            id="InvalidArgument",
        ),
        pytest.param(
            _server_error(oss_exc.ServerError, 400, "InvalidArgument"),
            ObjectFetchStatus.NOT_FOUND,
            "the object store refused the request (InvalidArgument)",
            id="bare-400-InvalidArgument",
        ),
        pytest.param(
            _server_error(oss_exc.ServerError, 400, ""),
            ObjectFetchStatus.NOT_FOUND,
            "the object store refused the request",
            id="bare-400-no-code",
        ),
        pytest.param(
            # A 301: the bucket lives in another region. Configuration, and
            # caught by code because no status range would catch it.
            _server_error(oss_exc.ServerError, 301, "PermanentRedirect"),
            ObjectFetchStatus.NOT_FOUND,
            "the object store refused the request (PermanentRedirect)",
            id="PermanentRedirect-301",
        ),
        pytest.param(
            # A local SDK error: the store never saw the request. Its own
            # sentence, pointing at the credential row and not at the bucket
            # policy — and a refusal all the same, since retrying changes
            # nothing. The SDK reports it with a negative status, so it is
            # ruled on before the status ranges.
            oss_exc.ClientError("The region should not be None in signature version 4."),
            ObjectFetchStatus.NOT_FOUND,
            "the request could not be built from the credential; check its "
            "endpoint, region and key pair",
            id="ClientError",
        ),
        pytest.param(
            oss_exc.RequestError(RequestsConnectionError(SDK_MESSAGE)),
            ObjectFetchStatus.UNAVAILABLE,
            "the object store could not be reached",
            id="RequestError",
        ),
        pytest.param(
            # Throttling arrives as a 4xx and is the store having a bad time,
            # not the document being wrong: maskable, ruled on before the
            # refused-request range.
            _server_error(oss_exc.ServerError, 429, "QpsLimitExceeded"),
            ObjectFetchStatus.UNAVAILABLE,
            "the object store is unavailable (QpsLimitExceeded)",
            id="throttled-429",
        ),
        pytest.param(
            _server_error(oss_exc.ServerError, 408, "RequestTimeout"),
            ObjectFetchStatus.UNAVAILABLE,
            "the object store is unavailable (RequestTimeout)",
            id="RequestTimeout-408",
        ),
        pytest.param(
            _server_error(oss_exc.ServerError, 500, "InternalError"),
            ObjectFetchStatus.UNAVAILABLE,
            "the object store is unavailable (InternalError)",
            id="5xx",
        ),
        pytest.param(
            _server_error(oss_exc.ServerError, 503, ""),
            ObjectFetchStatus.UNAVAILABLE,
            "the object store is unavailable",
            id="5xx-no-code",
        ),
        pytest.param(
            # A response the SDK could not parse: its own negative status,
            # no code. Nothing to refuse on, so maskable.
            oss_exc.OpenApiFormatError("unparseable body"),
            ObjectFetchStatus.UNAVAILABLE,
            "the object store returned an error",
            id="format-error",
        ),
    ],
)
def test_classify_pins_each_sdk_outcome_to_a_status_and_a_sentence(exc, status, sentence):
    store, _ = _store(**{"tools/qc.tgz": exc})

    result = store.get(_target(), "tools/qc.tgz", byte_limit=1024)

    assert result.status is status
    assert result.detail == WHERE + sentence
    assert result.content is None
    # The SDK's message is what carries the endpoint and the signed material.
    for leak in (ENDPOINT, SECRET, AK, "Signature="):
        assert leak not in result.detail


def test_a_local_sdk_error_is_a_refusal_keep_last_may_not_mask():
    store, _ = _store(k=oss_exc.ClientError("The endpoint you has specified is not valid"))
    assert store.get(_target(), "k", byte_limit=1).is_refusal is True


def test_an_unreachable_store_is_a_failure_not_a_refusal():
    store, _ = _store(k=oss_exc.RequestError(TimeoutError("connect")))
    result = store.get(_target(), "k", byte_limit=1)
    assert result.status is ObjectFetchStatus.UNAVAILABLE
    assert result.is_refusal is False


@pytest.mark.parametrize(
    "exc",
    [RequestsConnectionError("pool exhausted"), ConnectionRefusedError("refused")],
    ids=["requests", "os"],
)
def test_a_transport_error_the_sdk_did_not_wrap_is_still_a_failure(exc):
    store, _ = _store(k=exc)
    result = store.get(_target(), "k", byte_limit=1)
    assert result.status is ObjectFetchStatus.UNAVAILABLE
    assert result.detail == "object 'k' in bucket 'team-artifacts': the object store could not be reached"


def test_no_store_side_outcome_raises_out_of_the_read():
    """A missing object, a denied credential and an unreachable endpoint are
    all *results*: the caller's next move differs for each, and an exception
    would flatten them."""
    outcomes = [
        _server_error(oss_exc.NoSuchKey, 404, "NoSuchKey"),
        _server_error(oss_exc.AccessDenied, 403, "AccessDenied"),
        oss_exc.RequestError(OSError("dns")),
        oss_exc.ClientError("bucket_name is invalid"),
        oss_exc.InconsistentError("crc", "req-2"),
        _server_error(oss_exc.ServerError, 500, "InternalError"),
        RequestsConnectionError("raw"),
        _Stream(b"x", close_error=RequestsConnectionError("close reset")),
    ]
    for exc in outcomes:
        store, _ = _store(k=exc)
        assert isinstance(store.get(_target(), "k", byte_limit=1).status, ObjectFetchStatus)


# ── the real opener, with nothing in reach ───────────────────────────────────


def test_construction_touches_nothing():
    """An endpoint that cannot be reached must fail the *read* of one entry,
    not the allocation — one bad credential must not take down the apply
    that merely mentioned it. So nothing is opened until ``get``."""
    store, opener = _store(k=_Stream(b"x"))
    assert opener.opened == []
    store.get(_target(), "k", byte_limit=1)
    assert opener.opened == [_target()]


def test_a_row_stored_without_a_region_is_blamed_on_the_credential():
    """The latent bug this module's one rule forbids: a credential the surface
    accepted but cannot apply. ``region`` is required at PUT now; a row from
    before that reaches the client as the empty string, and the SDK's own
    precondition — signature version 4 needs a region — fires before any
    socket is opened. Reported as the credential's fault, never the bucket's,
    and never masked by ``keep_last``."""
    result = AliyunObjectStore().get(_target(region=""), "k", byte_limit=1024)

    assert result.status is ObjectFetchStatus.NOT_FOUND
    assert result.is_refusal
    assert "could not be built from the credential" in result.detail
    assert "region" in result.detail


def test_a_malformed_endpoint_is_blamed_on_the_credential():
    """The same class of error at the other field the credential supplies."""
    target = ObjectStoreTarget(
        endpoint="not a url",
        bucket="team-artifacts",
        access_key_id=AK,
        secret_access_key=SECRET,
        region="cn-shanghai",
    )
    result = AliyunObjectStore().get(target, "k", byte_limit=1024)

    assert result.status is ObjectFetchStatus.NOT_FOUND
    assert "could not be built from the credential" in result.detail
    assert "not a url" not in result.detail


# ── the stream and the cap ───────────────────────────────────────────────────


def test_a_present_object_is_found_and_carries_its_bytes():
    body = bytes(range(256)) * 3000  # several chunks at the client's granularity
    stream = _Stream(body, serve=object_store._CHUNK)
    store, _ = _store(k=stream)

    result = store.get(_target(), "k", byte_limit=len(body))

    assert result.status is ObjectFetchStatus.FOUND
    assert result.content == body
    assert stream.reads >= 3
    assert stream.closed  # the connection goes back, whatever the outcome


def test_an_object_exactly_at_the_cap_is_delivered():
    """The boundary is inclusive. Off by one here refuses a legitimate entry
    at exactly the documented category width."""
    store, _ = _store(edge=_Stream(b"y" * 1024))
    result = store.get(_target(), "edge", byte_limit=1024)
    assert result.status is ObjectFetchStatus.FOUND
    assert result.content is not None and len(result.content) == 1024


def test_an_oversized_object_is_refused_while_streaming_not_after():
    """The cap's whole purpose is that the bytes never reach the caller — and
    never reach memory either. The stream serves 512 bytes per read however
    much is asked for, so an implementation that buffered the object and then
    measured it would pull all 100 KiB; the client abandons the read having
    pulled no more than the cap plus one chunk."""
    stream = _Stream(b"x" * (100 * 1024), serve=512)
    store, _ = _store(big=stream)

    result = store.get(_target(), "big", byte_limit=4096)

    assert result.status is ObjectFetchStatus.TOO_LARGE
    assert result.content is None
    assert result.detail == (
        "object 'big' in bucket 'team-artifacts': the object exceeds the 4096-byte cap"
    )
    assert stream.served <= 4096 + 512
    assert stream.closed  # the rest stays on the wire, and the socket goes back


def test_a_read_that_dies_mid_stream_is_the_transport_not_the_document():
    stream = _Stream(b"x" * 4096, serve=1024, fail_after=2048)
    store, _ = _store(k=stream)

    result = store.get(_target(), "k", byte_limit=1 << 20)

    assert result.status is ObjectFetchStatus.UNAVAILABLE
    assert result.is_refusal is False
    assert result.detail == "object 'k' in bucket 'team-artifacts': the object read did not complete"
    assert stream.closed


def test_a_close_that_fails_does_not_take_the_verdict_with_it():
    """The bytes are already in hand when the socket is returned; a reset on
    close is logged and nothing else — it must not raise out of the read, and
    it must not turn a ``FOUND`` into a failure."""
    stream = _Stream(b"payload", close_error=RequestsConnectionError("reset on close"))
    store, _ = _store(k=stream)

    result = store.get(_target(), "k", byte_limit=1024)

    assert result.status is ObjectFetchStatus.FOUND
    assert result.content == b"payload"
    assert stream.closed


# ── report safety ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "outcome",
    [
        _server_error(oss_exc.AccessDenied, 403, "AccessDenied"),
        _server_error(oss_exc.NoSuchKey, 404, "NoSuchKey"),
        _server_error(oss_exc.InvalidArgument, 400, "InvalidArgument"),
        _server_error(oss_exc.ServerError, 429, "Throttling"),
        oss_exc.ClientError(f"bad endpoint {ENDPOINT} for {AK}"),
        oss_exc.RequestError(RequestsConnectionError(SDK_MESSAGE)),
        RequestsConnectionError(SDK_MESSAGE),
        _Stream(b"x" * 5000),
    ],
    ids=["denied", "missing", "refused", "throttled", "client", "unreachable", "raw", "too-large"],
)
def test_detail_never_carries_the_endpoint_or_the_secret(outcome):
    """``detail`` reaches an apply report. The bucket and key belong there;
    the endpoint and the credential's secret half never do — the same ruling
    the git road applies to stderr, which echoes the source URL."""
    store, _ = _store(k=outcome)
    result = store.get(_target(), "k", byte_limit=1024)

    assert result.status is not ObjectFetchStatus.FOUND
    assert SECRET not in result.detail
    assert ENDPOINT not in result.detail
    assert AK not in result.detail
    assert result.detail.startswith("object 'k' in bucket 'team-artifacts': ")
