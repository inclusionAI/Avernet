"""The store-backed identity and resource ports drive the real materialisers (W8).

No device anywhere: ``resolve`` / ``plan`` / ``write`` run against the store,
and convergence is observed from the store alone.
"""
from __future__ import annotations

import asyncio

from agentclaw.community.core.bot_config_manifest.sources.source_resolver import DeclaredSourceResolver
from agentclaw.community.core.apply_engine.managed_files import (
    CATEGORY_IDENTITY,
    CATEGORY_RESOURCES,
    ManagedFileScope,
    ManagedFilesStore,
)
from agentclaw.community.core.ports.resource_file_port import WorkspaceEntry
from agentclaw.community.core.apply_engine.managed_files.ports import (
    PlatformIdentity,
    PlatformResource,
)

from tests.community.core.apply_engine._fakes import (
    compiling_identity,
    compiling_resources,
    FakeCredentials,
    FakeManifestContent,
    make_context,
)

from ._fakes import FakeObjectStorage
from tests.community.core.apply_engine._fakes import FakeObjectStore

_BASE = "teclaw/dev/bolt_data"
# The identity and resources materialisers address the bot at ("staff", owner).
_SCOPE = ManagedFileScope(entity_type="staff", entity_id="u_owner", bot_id="b_1")


def _run(coro):
    return asyncio.run(coro)


def _store():
    oss = FakeObjectStorage()
    return ManagedFilesStore(object_storage=oss, store_base=lambda: _BASE), oss


def _identity(port):
    """The identity materialiser over the store port, fed through the real entry compiler."""
    content = FakeManifestContent()
    return compiling_identity(port, DeclaredSourceResolver(content, FakeCredentials(), FakeObjectStore()), content)


def _resources(port):
    content = FakeManifestContent()
    return compiling_resources(port, DeclaredSourceResolver(content, FakeCredentials(), FakeObjectStore()), content)


async def _apply(materialiser, ctx, entries):
    resolved = await materialiser.resolve(ctx, entries)
    assert resolved.ok, resolved.failures
    plan = await materialiser.plan(ctx, resolved.intents)
    written = await materialiser.write(ctx, plan)
    return plan, written


def _ctx():
    return make_context(engine_type="teclaw", owner_id="u_owner")


# ── identity ──────────────────────────────────────────────────────────────


def test_identity_converges_from_the_store_only() -> None:
    store, oss = _store()
    port = PlatformIdentity(store)
    materialiser = _identity(port)

    plan, written = _run(_apply(materialiser, _ctx(), [{"type": "RULES.md", "content": "# rules"}]))
    assert [(e.identity, e.outcome.value) for e in written] == [("RULES.md", "created")]
    rows = store.list(_SCOPE, category=CATEGORY_IDENTITY)
    assert [(r.name, r.rel_path) for r in rows] == [("RULES.md", "identity/RULES.md")]
    assert oss.objects[rows[0].store_key] == b"# rules"

    # Same content again: unchanged, no object write.
    puts_before = len(oss.puts)
    plan, written = _run(_apply(materialiser, _ctx(), [{"type": "RULES.md", "content": "# rules"}]))
    assert [e.outcome.value for e in written] == ["unchanged"]
    assert len(oss.puts) == puts_before

    # New content: updated; a second file: created.
    plan, written = _run(_apply(materialiser, _ctx(), [
        {"type": "RULES.md", "content": "# v2"},
        {"type": "SOUL.md", "content": "# soul"},
    ]))
    assert {(e.identity, e.outcome.value) for e in written} == {("RULES.md", "updated"), ("SOUL.md", "created")}

    # Declaring only SOUL.md removes RULES.md — the overwrite rule — as a
    # delete in the store, never an empty object.
    plan, written = _run(_apply(materialiser, _ctx(), [{"type": "SOUL.md", "content": "# soul"}]))
    assert plan.removals == ("RULES.md",)
    assert [r.name for r in store.list(_SCOPE, category=CATEGORY_IDENTITY)] == ["SOUL.md"]
    assert all("RULES.md" not in k for k in oss.objects)


def test_identity_declared_empty_removes_everything_but_reserved_names() -> None:
    store, _ = _store()
    port = PlatformIdentity(store)
    materialiser = _identity(port)
    _run(_apply(materialiser, _ctx(), [{"type": "RULES.md", "content": "# rules"}]))
    plan, written = _run(_apply(materialiser, _ctx(), []))
    assert plan.removals == ("RULES.md",)
    assert store.list(_SCOPE, category=CATEGORY_IDENTITY) == []


# ── resources ─────────────────────────────────────────────────────────────


def test_resources_write_rows_under_the_workspace_namespace() -> None:
    store, oss = _store()
    port = PlatformResource(store)
    materialiser = _resources(port)
    plan, written = _run(_apply(materialiser, _ctx(), [
        {"path": "data/kb/faq.md", "content": "q&a"},
        {"path": "data/notes.txt", "content": "n"},
    ]))
    assert {(e.identity, e.outcome.value) for e in written} == {
        ("data/kb/faq.md", "created"),
        ("data/notes.txt", "created"),
    }
    rows = store.list(_SCOPE, category=CATEGORY_RESOURCES)
    assert [r.rel_path for r in rows] == ["workspace/data/kb/faq.md", "workspace/data/notes.txt"]
    assert rows[0].ref_path == "staff_u_owner/b_1_manifest/teclaw/workspace/data/kb/faq.md"
    # A second apply classifies the present file as updated (v1 replaces).
    plan, written = _run(_apply(materialiser, _ctx(), [{"path": "data/kb/faq.md", "content": "q&a 2"}]))
    assert [e.outcome.value for e in written] == ["updated"]
    assert oss.objects[rows[0].store_key] == b"q&a 2"


def test_resource_tree_delete_is_a_prefix_delete_over_the_store() -> None:
    store, oss = _store()
    port = PlatformResource(store)
    args = dict(entity_type="staff", entity_id="u_owner", bot_id="b_1", engine_type="teclaw")
    _run(port.upload_file(**args, target_dir="kb", filename="a.md", data=b"a"))
    _run(port.upload_file(**args, target_dir="kb/deep", filename="b.md", data=b"b"))
    _run(port.upload_file(**args, target_dir="kb-old", filename="c.md", data=b"c"))
    assert _run(port.exists(**args, path="kb"))
    assert _run(port.exists(**args, path="kb/deep/b.md"))
    assert not _run(port.exists(**args, path="nope"))
    assert _run(port.delete(**args, path="kb"))
    assert not _run(port.delete(**args, path="kb"))
    assert [r.rel_path for r in store.list(_SCOPE, category=CATEGORY_RESOURCES)] == ["workspace/kb-old/c.md"]
    assert not _run(port.exists(**args, path="kb"))
    assert len(oss.objects) == 1


def test_platform_resource_lists_like_a_device_and_deletes_a_directory() -> None:
    """``list_entries`` shows the store's objects as a device listing would — a directory per intermediate
    segment, one level or every descendant — with no policy of its own; ``delete_dir`` removes every object
    under a path and answers whether there was any."""
    store, oss = _store()
    port = PlatformResource(store)
    coords = dict(entity_id="u_owner", bot_id="b_1", engine_type="teclaw")
    _run(port.upload_file(**coords, target_dir="data/kb", filename="faq.md", data=b"q"))
    _run(port.upload_file(**coords, target_dir="data", filename="notes.txt", data=b"n"))
    _run(port.upload_file(**coords, target_dir="", filename="top.txt", data=b"t"))

    assert _run(port.list_entries(**coords)) == (WorkspaceEntry("data", True), WorkspaceEntry("top.txt", False))
    assert _run(port.list_entries(**coords, recursive=True)) == (
        WorkspaceEntry("data", True),
        WorkspaceEntry("data/kb", True),
        WorkspaceEntry("data/kb/faq.md", False),
        WorkspaceEntry("data/notes.txt", False),
        WorkspaceEntry("top.txt", False),
    )
    assert _run(port.list_entries(**coords, prefix="data")) == (
        WorkspaceEntry("data/kb", True),
        WorkspaceEntry("data/notes.txt", False),
    )
    assert _run(port.delete_dir(**coords, path="data/kb")) is True
    assert _run(port.list_entries(**coords, recursive=True)) == (
        WorkspaceEntry("data", True),
        WorkspaceEntry("data/notes.txt", False),
        WorkspaceEntry("top.txt", False),
    )
    assert _run(port.delete_dir(**coords, path="data/kb")) is False
    assert _run(port.delete_dir(**coords, path="data")) is True
    assert [r.rel_path for r in store.list(_SCOPE, category=CATEGORY_RESOURCES)] == ["workspace/top.txt"]
