"""Manifest schema §5 limits — the half of them that a write can check.

§5 lists two kinds of limit and they are not interchangeable:

* **Write-time**: document size, entries per category, inline ``content`` size.
  Everything needed to check them arrives in the request, so they are refused at
  ``PUT`` and a caller learns the limit instead of discovering it at an apply
  point they are not watching.
* **Fetch-time**: per-entry download sizes, unpacked sizes, archive file counts,
  per-apply totals, timeouts. None of them are knowable here — this feature does
  no fetching at all (W1 is explicitly fetch-free) — so they belong to the
  fetcher (W2) and to apply (W4/W5), and are deliberately absent from this
  module rather than declared here and enforced nowhere.

Putting the second group here as constants "for later" would be the worse
mistake: a limit that is declared next to enforced ones reads as enforced.
"""
from __future__ import annotations

# The startup script's own cap, imported rather than restated. ``script.body``
# in a manifest *is* the #926 startup script (schema §3.6, work-items §2.2), so
# a second number here would be a second contract for one field — and the two
# would be free to drift the moment either moved.
#
# Imported from the owning core module, not from ``api/``: ``core`` must not
# import that layer, and the contract module is where the constant lives anyway.
from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (
    MAX_SCRIPT_BYTES,
)

#: Whole-document cap, UTF-8 bytes (schema §5). The script body is counted
#: inside it *and* capped separately: 24 KiB of script inside a 64 KiB document
#: is legal, 64 KiB of script is not.
MAX_DOCUMENT_BYTES = 64 * 1024

#: Entries per manifest category (schema §5). Applies per category, not to
#: their sum — a bot with 50 skills and 50 resources is within the limit.
MAX_ENTRIES_PER_CATEGORY = 50

#: One inline ``content`` block, UTF-8 bytes (schema §5).
#:
#: Equal to the document cap, which makes it unreachable on its own today: a
#: ``content`` block big enough to break this has already broken that. Kept
#: anyway, and said out loud rather than left to be discovered — the two are
#: independent knobs, the document cap is the one likelier to rise, and a
#: per-entry ceiling that only exists once the outer one moves is still the
#: rule §5 states.
MAX_INLINE_CONTENT_BYTES = 64 * 1024

#: One source URL, in characters (schema §5, and the column the provenance
#: log stores it into is the same width — one number, stated once).
#:
#: A length check belongs HERE, at admission, not only in the W11 store: the
#: store's refusal lands after the platform has already fetched (up to the
#: per-entry cap) — the expensive order — and a document this surface
#: accepts but every apply point rejects is exactly the shape the module's
#: one rule ("this surface never accepts something it cannot apply")
#: exists to forbid. The store keeps its check as the last line of defence
#: for what admission cannot see (a redirect ``Location``'s length); a
#: declared source's length, admission sees.
MAX_SOURCE_URL_CHARS = 2048

__all__ = [
    "MAX_DOCUMENT_BYTES",
    "MAX_ENTRIES_PER_CATEGORY",
    "MAX_INLINE_CONTENT_BYTES",
    "MAX_SCRIPT_BYTES",
    "MAX_SOURCE_URL_CHARS",
]
