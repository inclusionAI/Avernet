"""In-memory ObjectStoreClientFactory — the executable spec for the protocol.

Rule 25's conformance is *consumer ↔ Protocol* with the local impl standing in
for "what we believe prod does", so this impl is where each
:class:`ObjectFetchStatus` is given a reachable shape:

- an object that is present answers ``FOUND``
- one that is not answers ``NOT_FOUND``
- a target whose key pair does not match what the bucket was seeded with
  answers ``DENIED`` — credentials are compared rather than ignored, because a
  double that authorised everything would let a consumer bypass the credential
  entirely and still pass
- an object over the caller's ``byte_limit`` answers ``TOO_LARGE`` **without
  the caller ever holding it**
- a bucket a test marks unreachable answers ``UNAVAILABLE``

Every call is recorded so a conformance suite can assert the plugin was
actually invoked — the assertion Rule 25 names as the one that stops a
consumer silently bypassing the plugin.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.object_store_client import (
    ObjectFetchResult,
    ObjectFetchStatus,
    ObjectStoreClient,
    ObjectStoreClientFactory,
    ObjectStoreTarget,
)


@dataclass
class FakeBucket:
    """One bucket's contents and the credential entitled to read it."""

    objects: dict[str, bytes] = field(default_factory=dict)
    #: ``None`` means "any credential reads this bucket" — the shape a test
    #: that is not about authorisation wants.
    access_key_id: Optional[str] = None
    #: When set, every read answers ``UNAVAILABLE`` with this detail.
    unavailable: Optional[str] = None


class InMemoryObjectStoreClient(ObjectStoreClient):
    """One bucket's reader. Holds no connection and touches no network."""

    def __init__(
        self,
        target: ObjectStoreTarget,
        bucket: Optional[FakeBucket],
        record,
    ) -> None:
        self._target = target
        self._bucket = bucket
        self._record = record

    def get(self, key: str, *, byte_limit: int) -> ObjectFetchResult:
        self._record(self._target, key, byte_limit)
        where = f"{self._target.bucket}/{key}"

        if self._bucket is None:
            # An unknown bucket is not distinguishable from one this
            # credential may not see, and guessing which would leak the
            # difference. The store's own answer is the honest one.
            return ObjectFetchResult(
                ObjectFetchStatus.DENIED,
                detail=f"{where}: not authorized",
            )
        if self._bucket.unavailable is not None:
            return ObjectFetchResult(
                ObjectFetchStatus.UNAVAILABLE,
                detail=f"{where}: {self._bucket.unavailable}",
            )
        if (
            self._bucket.access_key_id is not None
            and self._bucket.access_key_id != self._target.access_key_id
        ):
            return ObjectFetchResult(
                ObjectFetchStatus.DENIED,
                detail=f"{where}: not authorized",
            )

        body = self._bucket.objects.get(key)
        if body is None:
            return ObjectFetchResult(
                ObjectFetchStatus.NOT_FOUND,
                detail=f"{where}: no such object",
            )
        if len(body) > byte_limit:
            # The bytes are NOT returned. A caller that received them would be
            # holding exactly what the cap exists to keep out of memory, and a
            # test asserting only on the status would not notice.
            return ObjectFetchResult(
                ObjectFetchStatus.TOO_LARGE,
                detail=(
                    f"{where}: object exceeds the {byte_limit}-byte cap "
                    f"({len(body)} bytes)"
                ),
            )
        return ObjectFetchResult(ObjectFetchStatus.FOUND, content=body)


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="in-memory buckets; every ObjectFetchStatus reachable by seeding",
)
class InMemoryObjectStoreClientFactory(ObjectStoreClientFactory):
    """Seed with :meth:`put`, then allocate clients the way production does."""

    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}
        #: ``(target, key, byte_limit)`` per read — the plugin-hit evidence a
        #: conformance suite asserts on.
        self.calls: list[tuple[ObjectStoreTarget, str, int]] = []

    # --- seeding ------------------------------------------------------------

    def put(
        self,
        bucket: str,
        key: str,
        content: bytes,
        *,
        access_key_id: Optional[str] = None,
    ) -> None:
        holder = self.buckets.setdefault(bucket, FakeBucket())
        holder.objects[key] = content
        if access_key_id is not None:
            holder.access_key_id = access_key_id

    def make_unavailable(self, bucket: str, detail: str = "endpoint unreachable") -> None:
        self.buckets.setdefault(bucket, FakeBucket()).unavailable = detail

    def reset(self) -> None:
        self.buckets.clear()
        self.calls.clear()

    # --- the protocol -------------------------------------------------------

    def client_for(self, target: ObjectStoreTarget) -> ObjectStoreClient:
        # Constructing a client touches nothing, exactly as the protocol
        # requires: an unreachable endpoint must fail one entry's read, not
        # the allocation that preceded it.
        return InMemoryObjectStoreClient(
            target, self.buckets.get(target.bucket), self._record
        )

    def _record(self, target: ObjectStoreTarget, key: str, byte_limit: int) -> None:
        self.calls.append((target, key, byte_limit))


__all__ = [
    "FakeBucket",
    "InMemoryObjectStoreClient",
    "InMemoryObjectStoreClientFactory",
]
