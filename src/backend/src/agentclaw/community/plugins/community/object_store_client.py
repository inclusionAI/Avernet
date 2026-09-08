"""Community ObjectStoreClientFactory — boto3 against an S3-compatible store.

Covers MinIO, AWS S3, Cloudflare R2, and Aliyun OSS's S3-compatible endpoints.
boto3 is imported lazily so a deploy that never installs the ``community``
dependency group can still import this module.

**What this does not cover, stated plainly.** boto3 signs AWS SigV4, so this
impl reaches a store only where the S3 protocol is served. Object stores whose
*native* API uses a different signing scheme — a different service name, scope
suffix and signing-key chain, and a canonical URI built from structured bucket
and key inputs rather than parsed from the URL — are not reachable through it.
A deploy pointing an ``oss`` source at such an endpoint gets ``DENIED``, which
is a legible outcome rather than a silent one; the fix is a deployment binding
its own native-SDK factory behind this same protocol, not a change here.

Two things this must get right, both carried over from the guarded fetcher
because dropping the request signer must not drop the protections around it:

1. **The cap is enforced while streaming.** ``StreamingBody`` is read in
   chunks and abandoned the moment it passes ``byte_limit`` — never
   ``body.read()`` unbounded, which would pay the memory the cap exists to
   refuse.
2. **Errors are classified, not swallowed.** The opposite of
   ``ObjectStoragePlugin``'s contract, and the reason this is a separate
   protocol: ``keep_last`` may mask an unreachable store and must not mask a
   denied credential.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.object_store_client import (
    ObjectFetchResult,
    ObjectFetchStatus,
    ObjectStoreClient,
    ObjectStoreClientFactory,
    ObjectStoreTarget,
)

logger = get_logger()

#: Read granularity. Small enough that one chunk past the cap is a bounded
#: overshoot, large enough not to make a 100 MB read a million calls.
_CHUNK = 256 * 1024

#: S3 error codes that mean "the document names something that is not there".
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "404", "NotFound"})

#: ...and that mean "this credential may not read it". Both halves matter:
#: a wrong key and a right key without permission are the same refusal to the
#: caller, and neither may be masked by ``keep_last``.
_DENIED_CODES = frozenset(
    {
        "AccessDenied",
        "AllAccessDisabled",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "InvalidSecurity",
        "403",
        "Forbidden",
    }
)


class S3ObjectStoreClient(ObjectStoreClient):
    """One bucket's reader over an S3-compatible endpoint."""

    def __init__(self, s3: Any, target: ObjectStoreTarget, errors) -> None:
        self._s3 = s3
        self._target = target
        self._client_error, self._boto_error = errors

    def get(self, key: str, *, byte_limit: int) -> ObjectFetchResult:
        where = f"{self._target.bucket}/{key}"
        try:
            response = self._s3.get_object(Bucket=self._target.bucket, Key=key)
        except self._client_error as exc:
            return self._classify(exc, where)
        except self._boto_error as exc:
            # Transport: DNS, TLS, connect timeouts, endpoint resolution. The
            # class keep_last exists for.
            logger.warning(
                "[manifest.object_store] unavailable bucket=%s type=%s",
                self._target.bucket,
                type(exc).__name__,
            )
            return ObjectFetchResult(
                ObjectFetchStatus.UNAVAILABLE,
                detail=f"{where}: the object store could not be reached",
            )

        body = response["Body"]
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
                        detail=(
                            f"{where}: object exceeds the {byte_limit}-byte cap"
                        ),
                    )
                chunks.append(chunk)
        except (self._client_error, self._boto_error, OSError) as exc:
            # A read that dies mid-stream is the transport, not the document.
            logger.warning(
                "[manifest.object_store] read failed bucket=%s type=%s",
                self._target.bucket,
                type(exc).__name__,
            )
            return ObjectFetchResult(
                ObjectFetchStatus.UNAVAILABLE,
                detail=f"{where}: the read did not complete",
            )
        finally:
            close = getattr(body, "close", None)
            if close is not None:
                close()

        return ObjectFetchResult(ObjectFetchStatus.FOUND, content=b"".join(chunks))

    def _classify(self, exc: Any, where: str) -> ObjectFetchResult:
        """An SDK error to a status — by code, never by its message.

        The message is what carries endpoints and, on some paths, signed query
        strings; ``detail`` is composed from the bucket, the key and the verdict
        instead. Same ruling ``fetch/git_source.py`` applies to git's stderr.
        """
        response = getattr(exc, "response", None) or {}
        code = str((response.get("Error") or {}).get("Code", ""))
        status = str(
            (response.get("ResponseMetadata") or {}).get("HTTPStatusCode", "")
        )
        if code in _NOT_FOUND_CODES or status == "404":
            return ObjectFetchResult(
                ObjectFetchStatus.NOT_FOUND, detail=f"{where}: no such object"
            )
        if code in _DENIED_CODES or status == "403":
            return ObjectFetchResult(
                ObjectFetchStatus.DENIED, detail=f"{where}: not authorized"
            )
        # Anything else — 5xx, throttling, an unmapped code — is the store
        # having a bad time rather than the document being wrong. Maskable,
        # which is the safe direction *for this bucket*: a refusal wrongly
        # called a failure keeps stale bytes for one apply; a failure wrongly
        # called a refusal fails an apply that would have recovered.
        logger.warning(
            "[manifest.object_store] unclassified bucket=%s code=%s status=%s",
            self._target.bucket,
            code or "?",
            status or "?",
        )
        return ObjectFetchResult(
            ObjectFetchStatus.UNAVAILABLE,
            detail=f"{where}: the object store returned an error",
        )


class S3ObjectStoreClientFactory(ObjectStoreClientFactory):
    """Allocates one boto3 client per target."""

    def client_for(self, target: ObjectStoreTarget) -> ObjectStoreClient:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError

        # Explicit credentials, never boto3's env chain: these come from a
        # tenant's credential row, and falling back to the process's ambient
        # AWS identity would read one tenant's bucket as the platform.
        client = boto3.client(
            "s3",
            endpoint_url=target.endpoint or None,
            region_name=target.region or None,
            aws_access_key_id=target.access_key_id,
            aws_secret_access_key=target.secret_access_key,
            config=Config(
                # Bounded like every other manifest fetch: an apply must not
                # sit on a hung endpoint past the lock TTL.
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 2, "mode": "standard"},
                # An unknown token cannot be resolved from the environment
                # either — the credential is the whole identity here.
                signature_version="s3v4",
            ),
        )
        return S3ObjectStoreClient(client, target, (ClientError, BotoCoreError))


__all__ = ["S3ObjectStoreClient", "S3ObjectStoreClientFactory"]
