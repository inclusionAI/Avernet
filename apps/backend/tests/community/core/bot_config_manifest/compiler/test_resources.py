"""The resources entry compiler: the root rule, the nesting ban, files and trees."""

from __future__ import annotations

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    DesiredResourceFile,
    DesiredResourceTree,
    DesiredState,
    StoredContent,
)
from agentclaw.community.core.bot_config_manifest.compiler.entries.resources import (
    OUTSIDE_ROOT_REASON,
    compile_resources,
)
from agentclaw.community.core.bot_config_manifest.compiler.session import digest_of
from agentclaw.community.core.content_store import canonical_tree_bytes, decode_tree_bytes
from tests.community.core.apply_engine._fakes import build_skill_tgz, oss_source
from tests.community.core.bot_config_manifest.compiler._rig import rig


@pytest.mark.asyncio
async def test_files_inline_and_fetched_land_in_the_store_under_the_root() -> None:
    session, content, resolver = rig({"faq.csv": b"q,a\n"})
    declared = await compile_resources(
        session,
        [
            {"path": "data/faq.csv", "source": oss_source("faq.csv")},
            {"path": "data/notes/hello.txt", "content": "hello\n"},
        ],
        resolver=resolver,
        content=content,
    )
    assert declared.failures == ()
    fetched, inline = declared.entries
    assert fetched == DesiredResourceFile("data/faq.csv", StoredContent(digest_of(b"q,a\n")), note=None)
    assert isinstance(inline, DesiredResourceFile) and content.read(inline.content.digest) == b"hello\n"
    DesiredState(resources=declared)  # the model accepts what the compiler emits


@pytest.mark.asyncio
async def test_a_directory_entry_becomes_one_reachable_tree() -> None:
    archive = build_skill_tgz([("prices/2026.csv", b"d,p\n"), ("prices/2025.csv", b"d,q\n")])
    session, content, resolver = rig({"prices.tgz": archive})
    declared = await compile_resources(
        session,
        [{"path": "data/prices/", "source": oss_source("prices.tgz"), "unpack": "tar.gz", "strip_components": 1}],
        resolver=resolver,
        content=content,
    )
    assert declared.failures == ()
    (tree,) = declared.entries
    assert isinstance(tree, DesiredResourceTree) and tree.path == "data/prices/"
    members = decode_tree_bytes(content.read(tree.tree.digest))
    assert members == [("2025.csv", b"d,q\n"), ("2026.csv", b"d,p\n")]
    assert tree.tree.digest == digest_of(canonical_tree_bytes(members))
    assert content.is_reachable(session.scope, tree.tree.digest)


@pytest.mark.asyncio
async def test_grammar_rules_are_re_asked_and_reported_per_entry() -> None:
    session, content, resolver = rig({"x": b"1"})
    declared = await compile_resources(
        session,
        [
            {"path": "notes/outside.txt", "content": "x"},
            {"path": "data/", "content": "x"},
            {"path": "../escape", "content": "x"},
            {"path": "data/dup.txt", "content": "x"},
            {"path": "data/dup.txt", "content": "y"},
            {"path": "data/tree/", "source": oss_source("x")},
            {"path": "data/tree/inner.txt", "content": "x"},
            {"content": "no path"},
            {"path": "data/missing.bin", "source": oss_source("absent")},
        ],
        resolver=resolver,
        content=content,
    )
    reasons = {f.identity: f.reason for f in declared.failures}
    assert reasons["notes/outside.txt"] == OUTSIDE_ROOT_REASON
    assert reasons["data/"] == OUTSIDE_ROOT_REASON
    assert "'..'" in reasons["../escape"]
    assert "more than once" in reasons["data/dup.txt"]
    assert "nests under" in reasons["data/tree/inner.txt"]
    assert "'path'" in reasons["[7]"]
    assert "data/missing.bin" in reasons
    # The oss directory without 'unpack' is refused before any fetch.
    assert "unpack" in reasons["data/tree/"]
    assert [e.path for e in declared.entries] == ["data/dup.txt"]
