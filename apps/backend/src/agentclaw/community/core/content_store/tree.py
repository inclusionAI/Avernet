"""acm-tree-v1: the one byte string a stored tree is.

The content store addresses bytes, not trees. A directory the platform
delivers or records — a git-fetched skill, a directory resource, a captured
skill tree — is stored as one blob in this framing, so that it has a stable
content address and so that the bytes read back are the members, not a guess
from a URL. The encoding is the store's blob format: the Manifest's apply
writes and reads it, and the Genome Registry hashes, diffs and patches it.
"""
from __future__ import annotations

from typing import Optional


#: Marks the stored form of a git-delivered tree, and opens every blob
#: :func:`canonical_tree_bytes` produces.
#:
#: The framing after it is ``<path length>:<path><byte length>:<bytes>``
#: repeated, members sorted by path, all lengths in decimal ASCII and paths in
#: UTF-8. A two-file tree, byte for byte::
#:
#:     canonical_tree_bytes([("faq.csv", b"q,a\n"), ("a.txt", b"hi")])
#:     # -> b'acm-tree-v1\n5:a.txt2:hi7:faq.csv4:q,a\n'
#:     #       magic       ^^^^^^^^^^^^ "a.txt" (5 bytes) -> b"hi" (2 bytes)
#:     #                               ^^^^^^^^^^^^^^^^^ "faq.csv" (7) ->
#:     #                                                 b"q,a\n" (4)
#:
#: Note the sort: ``a.txt`` is emitted first although it was passed second.
#:
#: Self-describing on purpose: ``keep_last`` reads a receipt back as plain
#: bytes with no idea what shape they are, and "guess from the URL" is not
#: identification. The version digit is what lets the framing change later
#: without a stored copy being decoded under the wrong rules.
TREE_MAGIC = b"acm-tree-v1\n"


def canonical_tree_bytes(members: list[tuple[str, bytes]]) -> bytes:
    """One deterministic byte string standing for a whole delivered tree.

    Takes ``(relative path, bytes)`` pairs in any order and answers the framing
    :data:`TREE_MAGIC` documents, which shows a worked two-file example.

    Called by: the Manifest's ``materialisers/resources`` for the bytes a
    git-delivered entry owes the store, and by the Genome Registry for every
    skill tree and directory resource a revision stores.

    Two jobs, and the second is why it is **reversible**:

    1. A stable content address for the store — the same tree must hash the
       same on every apply, and two different trees must not collide. Members
       are sorted by path and each is framed with its path, its length and its
       bytes: length-prefixed rather than delimiter-joined, because a delimiter
       is something a *path or a payload* could contain, and a tree that could
       be made to hash as another tree is a receipt that proves nothing.
    2. The bytes ``keep_last`` stands in with when a later git fetch fails.
       ``on_fetch_failure: keep_last`` is the default and it promises the bot
       keeps running what it has — a promise an unreadable receipt cannot keep.

    Never delivered to a bot as-is: :func:`decode_tree_bytes` turns it back
    into the members, and those are the intents.
    """
    parts: list[bytes] = [TREE_MAGIC]
    for rel, data in sorted(members):
        encoded = rel.encode("utf-8")
        parts.append(b"%d:%s%d:" % (len(encoded), encoded, len(data)))
        parts.append(data)
    return b"".join(parts)


def decode_tree_bytes(blob: bytes) -> Optional[list[tuple[str, bytes]]]:
    """The members back out, or ``None`` when these are not a canonical tree::

        decode_tree_bytes(b"acm-tree-v1\n5:a.txt2:hi7:faq.csv4:q,a\n")
        # -> [("a.txt", b"hi"), ("faq.csv", b"q,a\n")]

        decode_tree_bytes(b"PK\x03\x04...")   # a zip; no magic
        # -> None

    Called by: the Manifest's ``BlobDelivery.members``, first thing, to tell a
    stored git tree from an archive; and by the Genome Registry's diff, patch
    and spec checks to read a stored tree back.

    ``None`` rather than an exception: the caller is asking "is this a stored
    git tree or an archive?", and a shape it does not recognise is an answer,
    not a fault. Truncation and a bad length are the same answer — a receipt
    that does not decode cleanly must not half-deliver a tree.
    """
    if not blob.startswith(TREE_MAGIC):
        return None
    members: list[tuple[str, bytes]] = []
    at = len(TREE_MAGIC)
    try:
        while at < len(blob):
            before = at
            colon = blob.index(b":", at)
            name_len = int(blob[at:colon])
            # A **negative** length is the interesting one, not a large one.
            # ``int(b"-3")`` parses happily, and a negative size moved the
            # cursor *backwards* — the loop then read the same frame forever,
            # hanging the worker thread with the apply's lock held while more
            # applies queued behind it. These bytes are attacker-reachable:
            # they are whatever a bucket served for a ``resources`` directory
            # entry, decoded before any archive validation runs.
            if name_len < 0:
                return None
            at = colon + 1
            name = blob[at : at + name_len].decode("utf-8")
            at += name_len
            colon = blob.index(b":", at)
            size = int(blob[at:colon])
            if size < 0:
                return None
            at = colon + 1
            if at + size > len(blob):
                return None
            members.append((name, blob[at : at + size]))
            at += size
            if at <= before:
                # Belt: every frame must consume at least one byte. Two
                # explicit sign checks are easy to add a third case beside and
                # forget; this makes non-termination structurally impossible
                # rather than impossible-by-enumeration.
                return None
    except (ValueError, UnicodeDecodeError):
        return None
    return members


__all__ = ["TREE_MAGIC", "canonical_tree_bytes", "decode_tree_bytes"]
