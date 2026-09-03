"""The store-backed identity and resource ports drive the real materialisers (W8).

No device anywhere: ``resolve`` / ``plan`` / ``write`` run against the store,
and convergence is observed from the store alone.
"""
from __future__ import annotations

import asyncio

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import EntryFetcher
from agentclaw.community.core.bot_config_manifest.apply.materialisers.identity import (
    IdentityMaterialiser,
)
from agentclaw.community.core.bot_config_manifest.apply.materialisers.resources import (
    ResourcesMaterialiser,
)
from agentclaw.community.core.bot_config_manifest.managed_files import (
    CATEGORY_IDENTITY,
    CATEGORY_RESOURCES,
    ManagedFileScope,
    ManagedFilesStore,
)
from agentclaw.community.core.bot_config_manifest.managed_files.ports import (
    StoreIdentityPort,
    StoreResourcePort,
)

from tests.community.core.bot_config_manifest.apply._fakes import (
    FakeCredentials,
    FakeGuardedFetcher,
    FakeManifestContent,
    make_context,
)

from ._fakes import FakeObjectStorage

_BASE = "teclaw/dev/bolt_data"
# The identity and resources materialisers address the bot at ("staff", owner).
_SCOPE = ManagedFileScope(entity_type="staff", entity_id="u_owner", bot_id="b_1")


def _run(coro):
    return asyncio.run(coro)


def _store():
    oss = FakeObjectStorage()
    return ManagedFilesStore(object_storage=oss, store_base=lambda: _BASE), oss


def _fetcher():
    return EntryFetcher(FakeGuardedFetcher(), FakeManifestContent(), FakeCredentials())


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
    port = StoreIdentityPort(store)
    materialiser = IdentityMaterialiser(port, _fetcher())

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
    port = StoreIdentityPort(store)
    materialiser = IdentityMaterialiser(port, _fetcher())
    _run(_apply(materialiser, _ctx(), [{"type": "RULES.md", "content": "# rules"}]))
    plan, written = _run(_apply(materialiser, _ctx(), []))
    assert plan.removals == ("RULES.md",)
    assert store.list(_SCOPE, category=CATEGORY_IDENTITY) == []


# ── resources ─────────────────────────────────────────────────────────────


def test_resources_write_rows_under_the_workspace_namespace() -> None:
    store, oss = _store()
    port = StoreResourcePort(store)
    materialiser = ResourcesMaterialiser(port, _fetcher())
    plan, written = _run(_apply(materialiser, _ctx(), [
        {"path": "kb/faq.md", "content": "q&a"},
        {"path": "notes.txt", "content": "n"},
    ]))
    assert {(e.identity, e.outcome.value) for e in written} == {("kb/faq.md", "created"), ("notes.txt", "created")}
    rows = store.list(_SCOPE, category=CATEGORY_RESOURCES)
    assert [r.rel_path for r in rows] == ["workspace/kb/faq.md", "workspace/notes.txt"]
    assert rows[0].ref_path == "staff_u_owner/b_1_manifest/teclaw/workspace/kb/faq.md"
    # A second apply classifies the present file as updated (v1 replaces).
    plan, written = _run(_apply(materialiser, _ctx(), [{"path": "kb/faq.md", "content": "q&a 2"}]))
    assert [e.outcome.value for e in written] == ["updated"]
    assert oss.objects[rows[0].store_key] == b"q&a 2"


def test_resource_tree_delete_is_a_prefix_delete_over_the_store() -> None:
    store, oss = _store()
    port = StoreResourcePort(store)
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
