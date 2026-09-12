"""The object-store road — read one object out of a tenant-named bucket.

A manifest source on ``protocol: oss`` names a bucket and a key; the endpoint,
the region and the AK/SK come from the tenant's stored credential. This module
turns that pair into a read. It is one of the two transports that remain —
the other is :mod:`git_source` (git) — and like it, it is a plain core
class: there is one implementation, Aliyun OSS's native
protocol through ``oss2``, and nothing for a deploy profile to select.

**Why one implementation, and why this one.** The store that matters serves
Aliyun's native API — ``OSS4-HMAC-SHA256``, service ``oss``, scope
``…/aliyun_v4_request``. An S3-compatible client signs AWS SigV4 against it
and is answered with HTTP 400 ``InvalidArgument``, "Invalid signing region in
Authorization header": the two are not variants of one scheme, and no
endpoint or region setting bridges them. ``oss2`` is public (Apache-2.0, on
PyPI), so nothing forces this out of the repo, and a plugin seam whose only
purpose was to let a deployment swap the SDK had nothing left to select.
The cost is stated plainly: an S3-compatible store (MinIO, R2, AWS S3) is
not reachable from a manifest ``oss`` source any more.

**Why this is not** :class:`~agentclaw.community.plugin_api.object_storage.ObjectStoragePlugin`.
Three properties of that protocol each rule it out here, and they are
properties rather than accidents — it serves the platform's *own* bucket:

- It is a **singleton bound to one bucket** at DI wiring time. A manifest
  names a different bucket per source, so the reader here is built per
  target — one bucket, one endpoint, one credential — at the call.
- Its credentials come from **the SDK's standard env chain, never from
  config** — stated in its own community impl. Ours come from a tenant's
  credential row, decrypted at the call.
- Its error contract **swallows everything** into ``False`` / ``None`` / ``[]``
  so callers can decide policy. That is the wrong shape here for a specific
  reason: ``keep_last`` may mask exactly one of "missing", "denied" and
  "unreachable", and a protocol that returns ``None`` for all three cannot
  tell the fetch layer which one it got.

That last point is the whole design. :class:`ObjectFetchStatus` exists so the
apply layer can apply W5's ruling — a **failure** is the transport and
``keep_last`` may stand in for it; a **refusal** is configuration or the
document, and standing in for it silently is how a denied credential goes on
serving last apply's bytes for a year.

The surface is one method. Listing a prefix is deliberately absent: an ``oss``
directory entry declares an archive, so nothing consumes a listing yet, and
the rule this module's siblings state applies — *add a method as a new
consumer lights up; do not proactively mirror a full SDK*.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Optional

import oss2
import oss2.exceptions as oss_exc
from requests import RequestException

from agentclaw.community.log import get_logger

logger = get_logger()


@dataclass(frozen=True)
class ObjectStoreTarget:
    """Where to read, and as whom.

    Assembled by the caller from two places that stay separate on purpose: the
    **credential** supplies ``endpoint``, ``region`` and the key pair, and the
    **source declaration** supplies ``bucket``. A manifest therefore cannot
    choose which host its credential reaches — the property the signing road
    had to enforce per-fetch with an ``allowed_prefixes`` policy.

    **That is about the document, not about trust.** The endpoint is still
    tenant-supplied, just at a different surface: a credential is written by
    an authenticated tenant application. It is validated against the guarded
    fetcher's rules before it is stored, and this type assumes that has
    already happened — a target built from an unvalidated endpoint points the
    SDK wherever it says, including at link-local metadata.

    ``region`` is required, not defaulted: signature version 4 scopes every
    signature to a region, and the SDK refuses to sign without one. The
    credential surface requires it at ``PUT`` for exactly that reason. A row
    written before that rule existed reaches here with the empty string, and
    :meth:`AliyunObjectStore.get` reports it as the credential's fault.
    """

    endpoint: str
    bucket: str
    access_key_id: str
    #: Decrypted at the call site and never stored on anything longer-lived
    #: than this value. It has no representation in any record or log.
    secret_access_key: str
    region: str

    def __repr__(self) -> str:  # pragma: no cover - defensive, not behaviour
        # A target reaches a log or a traceback the moment something raises
        # while holding one, and the default dataclass repr would print the
        # secret. Redacting here means no caller has to remember to.
        return (
            f"ObjectStoreTarget(endpoint={self.endpoint!r}, "
            f"bucket={self.bucket!r}, access_key_id={self.access_key_id!r}, "
            f"region={self.region!r}, secret_access_key=<redacted>)"
        )


class ObjectFetchStatus(StrEnum):
    """What happened, at the granularity the apply layer's rules need.

    The split that matters is refusal vs failure, because ``keep_last`` may
    mask one and not the other:

    - ``NOT_FOUND``, ``DENIED``, ``TOO_LARGE`` are **refusals** — the document
      or the credential is wrong, and last apply's bytes must not stand in.
    - ``UNAVAILABLE`` is a **failure** — the store could not be reached, which
      is exactly what ``keep_last`` exists for.
    """

    FOUND = "found"
    NOT_FOUND = "not_found"
    DENIED = "denied"
    TOO_LARGE = "too_large"
    UNAVAILABLE = "unavailable"


#: The statuses ``keep_last`` must never mask. Named here rather than
#: re-derived at the call site: an implementation that adds a status without
#: ruling on it would otherwise default to maskable, which is the unsafe
#: direction.
REFUSAL_STATUSES: frozenset[ObjectFetchStatus] = frozenset(
    {
        ObjectFetchStatus.NOT_FOUND,
        ObjectFetchStatus.DENIED,
        ObjectFetchStatus.TOO_LARGE,
    }
)


@dataclass(frozen=True)
class ObjectFetchResult:
    """The outcome of one read. ``content`` is set only when ``FOUND``."""

    status: ObjectFetchStatus
    content: Optional[bytes] = None
    #: A short, report-safe sentence naming what happened — it is copied into
    #: the apply report an operator reads, so it must carry no secret. Built
    #: from the bucket, the key, the status and the store's error code and
    #: nothing else, for example::
    #:
    #:     "object 'tools/qc/v2.tgz' in bucket 'team-artifacts': the object
    #:      was not found (NoSuchKey)"
    #:     "object 'tools/qc/v2.tgz' in bucket 'team-artifacts': the
    #:      credential was denied (AccessDenied)"
    #:
    #: Never the SDK's own message, which echoes the endpoint and sometimes a
    #: signed query string carrying the key pair. The error code is safe: it
    #: is a closed vocabulary the store publishes, and it is the one word that
    #: identifies a wrong-signature or wrong-region request. The same ruling
    #: ``fetch/git_source.py`` applies to git's stderr, for the same reason.
    detail: str = ""

    @property
    def is_refusal(self) -> bool:
        """True when ``keep_last`` must not stand in for this outcome."""
        return self.status in REFUSAL_STATUSES


#: Read granularity. Small enough that one chunk past the cap is a bounded
#: overshoot, large enough not to make a 100 MB read a million calls.
_CHUNK = 256 * 1024

#: Bounded like every other manifest fetch: an apply must not sit on a hung
#: endpoint past the lock TTL. ``oss2`` hands the pair straight to ``requests``
#: as ``(connect, read)``.
_CONNECT_TIMEOUT_SECONDS = 10
_READ_TIMEOUT_SECONDS = 60

#: The store's own vocabulary, by verdict. Codes rather than the SDK's typed
#: exceptions alone: ``make_exception`` answers a bare ``ServerError`` for any
#: (status, code) pair it has no class for, so the code is the reliable key.
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "404", "NotFound"})

#: "This credential may not read it". Both halves matter: a wrong key and a
#: right key without permission are the same refusal to the caller, and
#: neither may be masked by ``keep_last``.
_DENIED_CODES = frozenset(
    {
        "AccessDenied",
        "AccessDeniedByBucketPolicy",
        "AccessKeyDisabled",
        "AllAccessDisabled",
        "InvalidAccessKeyId",
        "InvalidSecurity",
        "InvalidSecurityToken",
        "RequestTimeTooSkewed",
        "SecurityTokenExpired",
        "SignatureDoesNotMatch",
        "401",
        "403",
        "Forbidden",
    }
)

#: "The request itself is wrong" — a malformed bucket name, a bad argument, a
#: signature the store rejects because the region is wrong. Configuration,
#: not availability: retrying changes nothing, and letting ``keep_last`` mask
#: them is how a mistyped region goes on serving last apply's bytes
#: indefinitely while looking healthy. Listed by code and not only by status
#: range because ``PermanentRedirect`` arrives as a 301.
_INVALID_REQUEST_CODES = frozenset(
    {
        "AuthorizationHeaderMalformed",
        "AuthorizationQueryParametersError",
        "BadRequest",
        "InvalidArgument",
        "InvalidBucketName",
        "InvalidObjectName",
        "InvalidRequest",
        "MalformedXML",
        "MethodNotAllowed",
        "PermanentRedirect",
        "400",
    }
)

#: The store having a bad time — throttling, a timeout, an internal error.
#: Maskable: this is the class ``keep_last`` exists for, and several of them
#: arrive as 4xx (408, 429), which is why they are ruled on before the
#: refused-request range below.
_RETRYABLE_CODES = frozenset(
    {
        "InternalError",
        "QpsLimitExceeded",
        "RequestLimitExceeded",
        "RequestTimeout",
        "ServerBusy",
        "ServiceUnavailable",
        "SlowDown",
        "Throttling",
        "TooManyRequests",
    }
)
_RETRYABLE_STATUSES = frozenset({408, 425, 429})


def _with_code(sentence: str, code: str) -> str:
    """The store's error code appended, when it sent one.

    The code is what identifies a configuration mistake — ``InvalidArgument``
    is the whole diagnosis of a signature the store will not accept — and it
    is safe: a closed vocabulary, never the message that carries endpoints
    and signed material. Its absence from the report is what cost a
    debugging cycle once.
    """
    return f"{sentence} ({code})" if code else sentence


def _open_bucket(target: ObjectStoreTarget) -> Any:
    """One ``oss2.Bucket`` for this target — one bucket, one credential.

    The SDK receives structured endpoint, bucket and key inputs and its own
    native signer; no URL is assembled or forwarded here. Explicit
    credentials, never the SDK's env chain: these come from a tenant's
    credential row, and falling back to the process's ambient identity would
    read one tenant's bucket as the platform. Signature version 4 is the
    native scheme and ``region`` is what scopes it; an empty one is handed to
    the SDK as absent so its own precondition fires locally rather than a
    mis-scoped signature travelling to the store.
    """
    return oss2.Bucket(
        oss2.AuthV4(target.access_key_id, target.secret_access_key),
        target.endpoint,
        target.bucket,
        connect_timeout=(_CONNECT_TIMEOUT_SECONDS, _READ_TIMEOUT_SECONDS),
        region=target.region or None,
    )


class AliyunObjectStore:
    """Reads objects out of tenant-named buckets over Aliyun OSS's native API.

    One instance serves every target: the reader is allocated per call — an
    ``oss2.Bucket`` holds nothing but the endpoint, the bucket name and the
    signer, so allocating it is not a step worth separating from the read.
    Construction touches no network, and the SDK's own objects are built
    inside :meth:`get`: even SDK construction can reject a malformed target,
    and doing it there turns that into a refusal for one entry instead of
    aborting the apply that merely named the credential.

    ``_open`` is the test seam, the way :func:`.endpoint_guard.endpoint_refusal`
    takes a ``resolver``: production opens a real bucket, a unit test hands in
    one whose ``get_object`` raises a synthesised SDK error or serves a
    scripted stream. Nothing else about the class is configurable.

    Two things this must get right, both carried over from the guarded
    fetcher because dropping the request signer must not drop the protections
    around it:

    1. **The cap is enforced while streaming.** The body is read in chunks
       and abandoned the moment it passes ``byte_limit`` — never read
       unbounded, which would pay the memory the cap exists to refuse.
    2. **Errors are classified, not swallowed.** The opposite of
       ``ObjectStoragePlugin``'s contract: ``keep_last`` may mask an
       unreachable store and must not mask a denied credential.
    """

    def __init__(
        self, _open: Callable[[ObjectStoreTarget], Any] = _open_bucket
    ) -> None:
        self._open = _open

    def get(
        self, target: ObjectStoreTarget, key: str, *, byte_limit: int
    ) -> ObjectFetchResult:
        """Read the object at ``key`` in ``target``'s bucket, refusing past
        ``byte_limit``.

        ``key`` is the **object's full path inside the bucket** — everything
        after the bucket name, already composed from the source's ``key``
        prefix and the entry's own part. For
        ``oss://team-artifacts/tools/qc/v2.tgz`` the bucket is
        ``team-artifacts`` (on the target) and ``key`` is ``tools/qc/v2.tgz``.
        No leading slash, no scheme, no bucket.

        It is **not** the credential's ``access_key_id`` — review asked, and
        the collision of the word "key" between "object key" and "access key"
        is a fair thing to trip on. The key pair reaches the reader on
        :class:`ObjectStoreTarget`; nothing about the caller's identity
        travels through this argument.

        ``byte_limit`` is the category's per-entry cap
        (``FETCH_ENTRY_LIMITS``), and it is enforced **while streaming**: an
        implementation that buffers the whole object and then measures it
        has already paid the memory an oversized object costs, which is the
        cost the cap exists to avoid. ``TOO_LARGE`` comes back having read no
        more than the cap plus one chunk.

        Never raises for a store-side outcome — a missing object, a denied
        credential and an unreachable endpoint are all *results*, because the
        caller's next move differs for each and an exception flattens them.
        """
        try:
            body = self._open(target).get_object(key)
        except oss_exc.RequestError as exc:
            # Transport: DNS, TLS, connect and read timeouts — the SDK's own
            # wrapper for them. The class keep_last exists for.
            return self._unavailable(
                target, key, exc, "the object store could not be reached"
            )
        except oss_exc.OssError as exc:
            return self._classify(exc, target, key)
        except (RequestException, OSError) as exc:
            # The same transport failures the SDK did not get to wrap.
            return self._unavailable(
                target, key, exc, "the object store could not be reached"
            )

        try:
            chunks: list[bytes] = []
            seen = 0
            while True:
                chunk = body.read(_CHUNK)
                if not chunk:
                    break
                seen += len(chunk)
                if seen > byte_limit:
                    # Abandoned here, with at most one chunk of overshoot in
                    # hand and the rest still on the wire. The bytes are not
                    # returned: a caller holding them would be holding exactly
                    # what the cap refuses.
                    return ObjectFetchResult(
                        ObjectFetchStatus.TOO_LARGE,
                        detail=_detail(
                            target,
                            key,
                            f"the object exceeds the {byte_limit}-byte cap",
                        ),
                    )
                chunks.append(chunk)
        except (oss_exc.OssError, RequestException, OSError) as exc:
            # A read that dies mid-stream is the transport, not the document.
            return self._unavailable(
                target, key, exc, "the object read did not complete"
            )
        finally:
            self._close_quietly(body, target, key)

        return ObjectFetchResult(ObjectFetchStatus.FOUND, content=b"".join(chunks))

    def _classify(
        self, exc: oss_exc.OssError, target: ObjectStoreTarget, key: str
    ) -> ObjectFetchResult:
        """An SDK error to a status — by type, status and code, never by its
        message.

        The message is what carries endpoints and, on some paths, signed query
        strings; ``detail`` is composed from the bucket, the key, the verdict
        and the store's error code instead. Same ruling ``fetch/git_source.py``
        applies to git's stderr.
        """
        code = str(getattr(exc, "code", "") or "")
        status = _status_of(exc)

        if isinstance(exc, oss_exc.ClientError):
            # The SDK refused to build the request: no region, a malformed
            # endpoint, an empty bucket or key pair. The store never saw it,
            # so sending an operator to the bucket policy would be wrong —
            # the fault is in their own credential row. A refusal all the
            # same: retrying changes nothing and ``keep_last`` must not mask
            # it. Ruled on first, ahead of the status ranges, because the SDK
            # reports it with a negative status of its own.
            logger.warning(
                "[manifest.object_store] client error bucket=%s key=%s",
                target.bucket,
                key,
            )
            return ObjectFetchResult(
                ObjectFetchStatus.NOT_FOUND,
                detail=_detail(
                    target,
                    key,
                    "the request could not be built from the credential; "
                    "check its endpoint, region and key pair",
                ),
            )
        if code in _NOT_FOUND_CODES or status == 404:
            return ObjectFetchResult(
                ObjectFetchStatus.NOT_FOUND,
                detail=_detail(
                    target, key, _with_code("the object was not found", code)
                ),
            )
        if code in _DENIED_CODES or status in (401, 403):
            return ObjectFetchResult(
                ObjectFetchStatus.DENIED,
                detail=_detail(
                    target, key, _with_code("the credential was denied", code)
                ),
            )
        if (
            code in _RETRYABLE_CODES
            or status in _RETRYABLE_STATUSES
            or (status is not None and status >= 500)
        ):
            return self._unavailable(
                target,
                key,
                exc,
                _with_code("the object store is unavailable", code),
            )
        if code in _INVALID_REQUEST_CODES or (
            status is not None and 400 <= status < 500
        ):
            # A 4xx the store did not classify further is still the request
            # being wrong, and retrying it changes nothing. Refusing rather
            # than reporting UNAVAILABLE is what keeps ``keep_last`` from
            # masking a mistyped region or bucket name forever. The code
            # rides along because it is the diagnosis: ``InvalidArgument`` is
            # the one word that says "wrong signature scheme or region", and
            # a report without it sends an operator looking at the bucket.
            return ObjectFetchResult(
                ObjectFetchStatus.NOT_FOUND,
                detail=_detail(
                    target,
                    key,
                    _with_code("the object store refused the request", code),
                ),
            )
        # What is left is a response the SDK could not make sense of — a
        # negative status of its own, a 3xx with no code. Maskable, and only
        # this. An earlier version sent *everything* unmapped down this path
        # on the reasoning that masking is the safer direction; it is not,
        # for a configuration error, which never recovers on its own.
        return self._unavailable(
            target,
            key,
            exc,
            _with_code("the object store returned an error", code),
        )

    def _unavailable(
        self,
        target: ObjectStoreTarget,
        key: str,
        exc: BaseException,
        sentence: str,
    ) -> ObjectFetchResult:
        # Never log the SDK message: it may contain the endpoint or signed
        # request material. Bucket, key and exception type are report-safe.
        logger.warning(
            "[manifest.object_store] unavailable bucket=%s key=%s type=%s",
            target.bucket,
            key,
            type(exc).__name__,
        )
        return ObjectFetchResult(
            ObjectFetchStatus.UNAVAILABLE, detail=_detail(target, key, sentence)
        )

    @staticmethod
    def _close_quietly(body: Any, target: ObjectStoreTarget, key: str) -> None:
        """The connection goes back whatever the outcome — and a close that
        fails must not raise out of a read that already has its verdict."""
        close = getattr(body, "close", None)
        if close is None:
            return
        try:
            close()
        except (RequestException, OSError):
            logger.warning(
                "[manifest.object_store] close failed bucket=%s key=%s",
                target.bucket,
                key,
            )


def _detail(target: ObjectStoreTarget, key: str, sentence: str) -> str:
    """The report line: the bucket, the key and the verdict, nothing else."""
    return f"object {key!r} in bucket {target.bucket!r}: {sentence}"


def _status_of(exc: oss_exc.OssError) -> Optional[int]:
    try:
        return int(exc.status)
    except (TypeError, ValueError, AttributeError):
        return None


__all__ = [
    "REFUSAL_STATUSES",
    "AliyunObjectStore",
    "ObjectFetchResult",
    "ObjectFetchStatus",
    "ObjectStoreTarget",
]
