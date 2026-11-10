"""Diagnostics over the shared HMAC signing key.

Every component on the principal-token contract holds the same secret, and each
one logs :func:`key_fingerprint` of its copy at boot, so that "do both ends hold
the same key?" is answered by comparing two log lines rather than by printing a
credential.
"""

from __future__ import annotations

import hashlib

# What a fingerprint of no key at all reads as, so a log line never shows a
# hash of the empty string as though it were a configured key.
_UNSET_FINGERPRINT = "unset"

# RFC 7518 §3.2: an HMAC key for SHA-256 must be at least as long as the hash
# output. PyJWT warns below it, and it is the threshold the fingerprint's
# safety argument rests on — see :func:`key_fingerprint`.
MIN_SIGNING_KEY_BYTES = 32


def is_weak_signing_key(key: str) -> bool:
    """Whether ``key`` is below the strength the shared-secret contract assumes.

    Length is a proxy for entropy, and a coarse one — it cannot tell 32 random
    bytes from 32 repetitions of ``a``. It is still worth checking, because the
    keys that show up short are overwhelmingly the human-chosen ones, and a
    check that catches the common case beats a docstring that assumes the
    problem away.

    Measured in **bytes, not characters**, because bytes are what HMAC
    consumes: a non-ASCII passphrase carries more bytes than it has characters,
    so counting characters would reject a key the algorithm is content with.
    """
    return 0 < len(key.encode("utf-8")) < MIN_SIGNING_KEY_BYTES


def key_fingerprint(key: str) -> str:
    """A short, non-reversible fingerprint of a shared signing key.

    **This algorithm is a cross-component contract.** Every component computes
    it over its own copy of the key and logs it at boot. The gateway still
    carries its own copy (``plugins/principal_signer/bare/_plugin.py``) until it
    moves onto this package; change the algorithm on one side only and the
    comparison silently becomes meaningless — the golden values pinned in the
    test suites exist to stop that.

    Safe to log **for a key of the mandated strength**, and that qualifier is
    the whole of the argument: SHA-256 is not reversible, so against ≥32 random
    bytes (RFC 7518 §3.2) a truncated digest is no practical brute-force oracle
    for someone who can already read the logs. Against a short or human-chosen
    key it *is* one — 32 bits is enough to confirm a dictionary guess offline,
    and confirming the shared secret means being able to forge identity tokens.

    Nothing in the type system enforces that strength, so
    :func:`is_weak_signing_key` exists and boot paths warn when a key falls
    below it rather than leaving the precondition asserted-but-unchecked. The
    fingerprint is still emitted for a weak key: suppressing it would remove the
    diagnostic exactly when a deployment is most misconfigured, and the answer
    to a weak shared secret is to replace it.

    Safe to log is not safe to *serve* — never put this on an HTTP endpoint,
    where it would let an unauthenticated caller confirm a guessed key.
    """
    if not key:
        return _UNSET_FINGERPRINT
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
