"""acm-tree-v1: the framing a stored tree is (``content_store/tree.py``).

Pinned here because two consumers depend on the same three properties: the
Manifest's ``keep_last`` reads a receipt back as plain bytes and must tell a
stored tree from an archive, and the Genome Registry hashes a skill tree or a
directory resource by this framing and must get the same digest on every
capture.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.content_store import (
    TREE_MAGIC,
    canonical_tree_bytes,
    decode_tree_bytes,
)


def test_the_framing_is_the_documented_one_byte_for_byte():
    assert TREE_MAGIC == b"acm-tree-v1\n"
    assert canonical_tree_bytes([("faq.csv", b"q,a\n"), ("a.txt", b"hi")]) == (
        b"acm-tree-v1\n5:a.txt2:hi7:faq.csv4:q,a\n"
    )


def test_the_canonical_form_round_trips_and_sorts():
    members = [("z.md", b"last"), ("a/b.md", b"first")]
    assert decode_tree_bytes(canonical_tree_bytes(members)) == sorted(members)


def test_two_different_trees_cannot_be_made_to_hash_alike():
    """Length-prefixed, not delimiter-joined: a delimiter is something a path
    or a payload could contain, and a tree that could be made to frame as
    another tree is a receipt that proves nothing."""
    assert canonical_tree_bytes([("a", b"b"), ("c", b"d")]) != canonical_tree_bytes(
        [("a", b"b\nc"), ("", b"d")]
    )


@pytest.mark.parametrize(
    "blob",
    [b"", b"PK\x03\x04 a real zip", canonical_tree_bytes([("a.md", b"0123456789")])[:-4]],
)
def test_what_is_not_a_canonical_tree_decodes_to_none(blob):
    """``None``, never an exception: the caller is asking "is this a stored
    tree or an archive?", and a shape it does not recognise is an answer. A
    truncated one is the same answer — a receipt that does not decode cleanly
    must not half-deliver a tree."""
    assert decode_tree_bytes(blob) is None
