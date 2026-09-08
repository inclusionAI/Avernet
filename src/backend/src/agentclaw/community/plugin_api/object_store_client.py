"""ObjectStoreClient — read one object out of a tenant-named bucket.

A manifest source on ``protocol: oss`` names a bucket and a key; the endpoint,
the region and the AK/SK come from the tenant's stored credential. This plugin
turns that pair into a read.

**Why this is not** :class:`~agentclaw.community.plugin_api.object_storage.ObjectStoragePlugin`.
Three properties of that protocol each rule it out here, and they are
properties rather than accidents — it serves the platform's *own* bucket:

- It is a **singleton bound to one bucket** at DI wiring time. A manifest
  names a different bucket per source, so the client has to be allocated per
  credential, not per deployment.
- Its credentials come from **boto3's standard env chain, never from config** —
  stated in its own community impl. Ours come from a tenant's credential row,
  decrypted at the call.
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
this protocol's sibling states the rule — *add a method as a new consumer
lights up; do not proactively mirror a full SDK*.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


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
    already happened — a target built from an unvalidated endpoint points
    ``boto3`` wherever it says, including at link-local metadata.
    """

    endpoint: str
    bucket: str
    access_key_id: str
    #: Decrypted at the call site and never stored on anything longer-lived
    #: than this value. It has no representation in any record or log.
    secret_access_key: str
    region: Optional[str] = None

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


#: The statuses ``keep_last`` may **not** mask. Named here rather than
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
    #: Report-safe by construction: composed from the bucket, the key and the
    #: status — never from the SDK's own message, which echoes endpoints and
    #: sometimes signed query strings. The same ruling ``fetch/git_source.py``
    #: applies to git's stderr, for the same reason.
    detail: str = ""

    @property
    def is_refusal(self) -> bool:
        """True when ``keep_last`` must not stand in for this outcome."""
        return self.status in REFUSAL_STATUSES


@runtime_checkable
class ObjectStoreClient(Protocol):
    """A reader bound to one bucket, on one endpoint, as one credential."""

    def get(self, key: str, *, byte_limit: int) -> ObjectFetchResult:
        """Read the object at ``key``, refusing past ``byte_limit``.

        ``byte_limit`` is the category's per-entry cap
        (``FETCH_ENTRY_LIMITS``), and it must be enforced **while streaming**:
        an implementation that buffers the whole object and then measures it
        has already paid the memory an oversized object costs, which is the
        cost the cap exists to avoid. ``TOO_LARGE`` comes back having read no
        more than the cap plus one chunk.

        Never raises for a store-side outcome — a missing object, a denied
        credential and an unreachable endpoint are all *results*, because the
        caller's next move differs for each and an exception flattens them.
        """
        ...


@runtime_checkable
class ObjectStoreClientFactory(Plugin, Protocol):
    """Allocates a client per target. **Not** a singleton client.

    The factory is the singleton; what it returns is not. That is the whole
    difference from :class:`ObjectStoragePlugin`, and it is what lets one
    apply read two buckets under two credentials.
    """

    def client_for(self, target: ObjectStoreTarget) -> ObjectStoreClient:
        """A client for this target. Constructing one must not touch the
        network — an unreachable endpoint is discovered by
        :meth:`ObjectStoreClient.get` and reported as ``UNAVAILABLE``, so a
        credential that cannot be used still fails one entry rather than the
        apply that allocated it."""
        ...


__all__ = [
    "REFUSAL_STATUSES",
    "ObjectFetchResult",
    "ObjectFetchStatus",
    "ObjectStoreClient",
    "ObjectStoreClientFactory",
    "ObjectStoreTarget",
]
