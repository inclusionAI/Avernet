"""The content store's own bounds: its address grammar and its audit identity width.

Two values, and both are shared with the store's consumers rather than copied
by them: the Manifest's fetch layer re-exports ``DIGEST_RE`` (W2 minted the
vocabulary; the store addresses by it), and the Genome Registry's limits
re-export ``MAX_AUDIT_ACTOR_CHARS`` (its actor columns are sized by the same
bound as the store's ``modifier``). One definition each, no per-module copies
that drift.
"""
from __future__ import annotations

import re

#: The digest vocabulary W2 fixed and every later wave shares: ``sha256:``
#: followed by exactly 64 lowercase hex. The fetcher mints these addresses
#: (declared-digest validation, FetchedObject.sha256) and the content store
#: uses them as its addressing scheme — one regex, one vocabulary.
#:
#: ``\A``/``\Z`` rather than ``^``/``$``: in Python ``$`` also matches
#: immediately before a trailing newline, so a digest with a ``\n`` tail
#: would pass and then fail downstream as a mismatch or a missing address —
#: the wrong taxonomy for malformed config. A fixed-width vocabulary has no
#: room for a newline.
DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")

#: Width of every audit-identity column the store and the Genome Registry
#: write: ``ac_manifest_content.modifier`` and the genome's ``created_actor``,
#: ``moved_actor`` and ``actor_id``. An application acting for a user records
#: ``app:<id>:on-behalf-of:<user>``; the application id is a bigint (at most
#: 20 digits) and the longest user id the access layer admits is 1024
#: characters, so the composite is at most 4 + 20 + 14 + 1024 = 1062 — this
#: bound holds it. The Genome Registry refuses a longer identity up front
#: (``Provenance``) rather than failing inside a write; the store refuses it
#: before the blob is written (``_require_fits``).
MAX_AUDIT_ACTOR_CHARS = 1100

__all__ = ["DIGEST_RE", "MAX_AUDIT_ACTOR_CHARS"]
