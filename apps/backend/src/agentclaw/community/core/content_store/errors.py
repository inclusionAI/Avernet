"""Error vocabulary for the manifest content store (W11, #1510).

Messages in this family may carry a digest, a sanitized URL (scheme, host,
path — never userinfo, never a query string), and a credential *name*. There
are no secret values to leak by construction: the store holds bytes, and the
only credential-shaped thing it ever sees is the name W3 minted.
"""
from __future__ import annotations


class ContentStoreError(ValueError):
    """Invalid input to the content store (400-class).

    A malformed digest is the canonical case: the digest IS the address, so
    an address that cannot name content is refused before anything touches
    the filesystem. Catch this family for caller-fault handling — and know
    what it does NOT include: platform-side failure is
    :class:`ContentStoreFault` and its subclasses, which deliberately do
    not inherit here, so a single ``except ContentStoreError`` cannot
    answer for disk damage it should be escalating.
    """


class ContentMissingError(ContentStoreError):
    """The digest names nothing in this store (404).

    Deliberately loud and deliberately final — the store never fetches on
    the read path (§2.8: 下发 reads the platform copy; re-fetching would
    recouple delivery to source-side faults, which is exactly what this
    layer exists to prevent). The caller decides what a missing receipt
    means; for keep_last that is W4's policy, not a silent side fetch.
    """


class ContentStoreFault(RuntimeError):
    """The store's own platform side failed (500-class).

    Sibling to :class:`ContentStoreError`, deliberately not its subclass: a
    hierarchy that nests a 500-class failure under a 400-class base arms
    every consumer that maps the base to "caller error" — the one failure
    in this family that should page someone would be reported to the caller
    as their own mistake. And the mirror hazard is as wrong: a consumer
    catching the 400 base to clean up would silently miss this one
    entirely. Siblings, so both mistakes are impossible.
    """


class ContentIntegrityError(ContentStoreFault):
    """Bytes under an address do not hash to that address (500-class).

    Raised when a fetched receipt disagrees with its own bytes at store
    time, or when a stored blob hashes differently on read — a blob the
    store itself verified when writing it. Either means disk corruption or a
    corrupted hand-off, and both are the "loud, never silently pass bytes"
    case: a delivery that cannot prove its content must fail rather than
    hand over what it merely believes.
    """
