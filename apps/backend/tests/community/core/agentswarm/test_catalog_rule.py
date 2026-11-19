"""The shared catalog-entry rule, and its known prefix limitation.

Dispatch and the run read surface both call ``find_catalog_entry`` so they
cannot disagree. The prefix branch returns the first match; the last test pins
that behaviour so a future fix (preferring the longest prefix) updates a test
rather than silently changing what runs execute.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.agentswarm.catalog_rule import find_catalog_entry
from agentclaw.community.core.agentswarm.domain import models as dm


def _catalog(*names):
    return tuple(dm.AgentSpec(name=n, role="r") for n in names)


def test_exact_name_wins():
    catalog = _catalog("coder", "coder_rust")
    assert find_catalog_entry(catalog, "coder_rust").name == "coder_rust"


def test_spawn_suffix_resolves_to_its_entry():
    catalog = _catalog("hello_writer", "brave_writer")
    spawn = "hello_writer_748ced10ec73"
    assert find_catalog_entry(catalog, spawn).name == "hello_writer"


def test_unknown_name_resolves_to_nothing():
    assert find_catalog_entry(_catalog("coder"), "nobody_abc") is None


def test_empty_catalog():
    assert find_catalog_entry((), "coder_abc") is None


def test_prefix_ambiguity_is_pinned():
    """KNOWN LIMITATION: the shorter prefix wins, not the longest match.

    With catalogs ``coder`` and ``coder_rust``, a ``coder_rust_ab12`` spawn
    currently resolves to ``coder``. That is pre-existing dispatch behaviour,
    shared here on purpose; fixing it is a separate change.
    """
    catalog = _catalog("coder", "coder_rust")
    assert find_catalog_entry(catalog, "coder_rust_ab12").name == "coder"


def test_reversed_catalog_order_changes_the_answer():
    """The real problem: the answer depends on roster order, not specificity."""
    catalog = _catalog("coder_rust", "coder")
    assert find_catalog_entry(catalog, "coder_rust_ab12").name == "coder_rust"
