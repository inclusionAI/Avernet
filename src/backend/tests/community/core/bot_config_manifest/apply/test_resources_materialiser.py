"""Tests for the ``resources`` materialiser (W6).

Pins, by the work item's acceptance criteria:
- file entries materialise through ``ResourceFileService`` at a
  workspace-relative ``path`` (physical placement stays the engine's call);
- directory entries converge as the whole archive: the tree under ``path`` is
  replaced in full, including hand-added files (ownership rule);
- fetch failures abort the whole category before the first write (§3.2);
- the platform unpacks to a temporary location — a bad archive or failed fetch
  never reaches the bot;
- convergence writes are file-grained (teclaw per-file forwarding is the same
  transport), and the module never touches ``BotConfigArtifact``;
- archive limits (member count / unpacked size) apply with W1's keys.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetchError,
    FetchedEntry,
)
from agentclaw.community.core.bot_config_manifest.apply.materialisers.resources import (
    _DECLARED_TREE,
    ResourcesMaterialiser,
)
from agentclaw.community.core.bot_config_manifest.apply.order import steps_for
from agentclaw.community.core.bot_config_manifest.apply.orchestrator import (
    ApplyOrchestrator,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    build_materialisers,
)

from ._fakes import (
    FakeActivationService,
    FakeCapabilityReader,
    FakeCredentials,
    FakeGuardedFetcher,
    FakeIdentityService,
    FakeManifestContent,
    FakeMcpAuth,
    FakeResourceFileService,
    FakeSkillUploadService,
    FakeStartupScriptService,
    build_skill_tgz,
    make_context,
    real_validator,
)


def _run(coro):
    return asyncio.run(coro)


class _StubEntryFetcher:
    """``EntryFetcher``'s test double, every call recorded.

    URLs ending in ``gone`` stand in for unreachable sources. With
    ``fixed_body``, every URL serves the same bytes (an archive); without it,
    the URL's own ``bytes-of-<url>``. It answers in the pipeline's own
    currency — a real :class:`FetchedEntry` — because the materialiser
    consumes ``.content``; a stub answering in the transport's
    ``FetchedObject`` shape would pass collection and fail on the first real
    resolve.
    """

    def __init__(self, fixed_body: bytes | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fixed_body = fixed_body

    def fetch(
        self,
        ctx: Any,
        *,
        source_url: str,
        digest: str | None = None,
        auth: str | None = None,
        category: str = "resources_file",
        keep_last: bool = False,
        entry_identity: str | None = None,
    ) -> FetchedEntry:
        self.calls.append(
            {
                "source_url": source_url,
                "digest": digest,
                "auth": auth,
                "category": category,
                "keep_last": keep_last,
                "entry_identity": entry_identity,
            }
        )
        if source_url.endswith("gone"):
            raise EntryFetchError("source unreachable")
        body = (
            self._fixed_body
            if self._fixed_body is not None
            else b"bytes-of-" + source_url.encode()
        )
        return FetchedEntry(content=body, digest="sha256:stub", from_store=False)


def _tgz(member: dict[str, bytes]) -> bytes:
    """A real tar.gz carrying ``member`` — true bytes, not synthetic objects."""
    return build_skill_tgz(list(member.items()))


def test_fake_resource_service_records_uploads_and_deletes():
    svc = FakeResourceFileService(exists_paths={"data/old.bin"})
    ctx = make_context(engine_type="claude_code")
    _run(
        svc.upload_file(
            entity_id=ctx.entity_id,
            bot_id=ctx.bot_id,
            engine_type=ctx.engine_type,
            target_dir="data",
            filename="a.bin",
            data=b"hello",
        )
    )
    _run(
        svc.delete(
            entity_id=ctx.entity_id,
            bot_id=ctx.bot_id,
            engine_type=ctx.engine_type,
            path="data/old.bin",
        )
    )
    assert svc.writes == {("data", "a.bin"): b"hello"}
    assert svc.deleted == ["data/old.bin"]
    # Deleted paths report absent — the fake must contract this, because the
    # plan stage's classification reads ``exists`` and would otherwise call
    # a deleted file "unchanged".
    assert (
        _run(
            svc.exists(
                entity_id=ctx.entity_id,
                bot_id=ctx.bot_id,
                engine_type=ctx.engine_type,
                path="data/old.bin",
            )
        )
        is False
    )


# --- file entries: URL fetch and inline content (Task 2) ---


def test_file_entry_from_url_resolves_to_intent_bytes():
    svc = FakeResourceFileService()
    stub = _StubEntryFetcher()
    m = ResourcesMaterialiser(svc, stub)
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(ctx, [{"path": "data/a.md", "source": "https://x/a.bin"}])
    )
    assert resolved.ok
    assert [i.identity for i in resolved.intents] == ["data/a.md"]
    assert resolved.intents[0].value == b"bytes-of-https://x/a.bin"
    # The funnel saw the entry's own identity under the file form's own
    # category — the W11 apply/entry linkage — with keep_last as the
    # declared default.
    assert stub.calls == [
        {
            "source_url": "https://x/a.bin",
            "digest": None,
            "auth": None,
            "category": "resources_file",
            "keep_last": True,
            "entry_identity": "data/a.md",
        }
    ]


def test_file_entry_inline_content_never_fetches():
    svc = FakeResourceFileService()
    stub = _StubEntryFetcher()
    m = ResourcesMaterialiser(svc, stub)
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "notes/r.md", "content": "# rules"}]))
    assert resolved.ok
    assert [i.identity for i in resolved.intents] == ["notes/r.md"]
    assert resolved.intents[0].value == b"# rules"
    assert stub.calls == []  # inline content: no fetch, ever


def test_fetch_failure_aborts_whole_category():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {"path": "data/a.md", "source": "https://x/a.bin"},
                {"path": "data/gone.md", "source": "https://x/gone"},
            ],
        )
    )
    assert not resolved.ok
    assert [f.identity for f in resolved.failures] == ["data/gone.md"]


def test_bad_path_entry_is_a_resolve_failure():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": 123}]))
    assert not resolved.ok


# --- directory entries: platform-side unpack (Task 3) ---


def test_dir_entry_expands_members_under_path():
    archive = _tgz({"top/a.txt": b"AAA", "top/sub/b.txt": b"BBB"})
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {
                    "path": "wrap/",
                    "unpack": "tar.gz",
                    "strip_components": 1,
                    "source": "https://x/tree.tgz",
                }
            ],
        )
    )
    assert resolved.ok, [f.reason for f in resolved.failures]
    got = {i.identity: i.value for i in resolved.intents}
    # The "wrap/" entry is the declared-tree marker: an explicit object,
    # never ``None`` (the dataclass default) — a future intent constructed
    # without an explicit value must not silently promise a tree deletion.
    assert got == {
        "wrap/": _DECLARED_TREE,
        "wrap/a.txt": b"AAA",
        "wrap/sub/b.txt": b"BBB",
    }
    assert resolved.intents[0].identity == "wrap/"
    assert resolved.intents[0].value is _DECLARED_TREE


def test_bad_archive_is_a_resolve_failure_and_nothing_reaches_the_bot():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(b"not-an-archive"))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [{"path": "wrap/", "unpack": "tar.gz", "source": "https://x/tree.tgz"}],
        )
    )
    assert not resolved.ok
    assert resolved.failures[0].identity == "wrap/"
    assert svc.writes == {} and svc.deleted == []


def test_dir_unpack_missing_is_rejected_at_resolve():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(_tgz({"a.txt": b"x"})))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(ctx, [{"path": "wrap/", "source": "https://x/tree.tgz"}])
    )
    assert not resolved.ok
    assert "unpack" in resolved.failures[0].reason


def test_nested_paths_abort_category():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(_tgz({"a.txt": b"x"})))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {
                    "path": "wrap/",
                    "unpack": "tar.gz",
                    "source": "https://x/t.tgz",
                },
                {"path": "wrap/inner.txt", "source": "https://x/i.txt"},
            ],
        )
    )
    assert not resolved.ok
    assert any("nest" in f.reason for f in resolved.failures)


# --- plan: classification for the report (Task 4) ---


def test_plan_classifies_present_as_updated_and_new_as_created():
    svc = FakeResourceFileService(exists_paths={"data/a.md"})
    m = ResourcesMaterialiser(svc, _StubEntryFetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {"path": "data/a.md", "source": "https://x/a.bin"},
                {"path": "data/new.md", "source": "https://x/new.bin"},
            ],
        )
    )
    assert resolved.ok, [f.reason for f in resolved.failures]
    plan = _run(m.plan(ctx, resolved.intents))
    by_id = {p.intent.identity: p.outcome for p in plan.entries}
    # Never "unchanged": v1 replaces on every apply by design (the work
    # item's recommended option), and a plan that claimed otherwise would
    # be a promise the write stage does not honour.
    assert by_id == {"data/a.md": "updated", "data/new.md": "created"}
    assert plan.removals == ()
    assert not plan.is_noop


def test_plan_addresses_the_bot_owner_not_the_manifest_storage_key():
    """``entity_id`` must be the owner (the router's address), not ``ctx.entity_id``.

    The two vocabularies share a name: ``ApplyContext.entity_id`` is the
    manifest's *storage key*, while ``ResourceFileService`` addresses a
    workspace by its *owner* — the address the resources router's
    ``_resolve_params`` resolves and ``resource_coords_from_record``
    derives. A test that left them equal would never catch the swap.
    """
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher())
    ctx = make_context(
        bot_id="b_1",
        owner_id="u_owner",
        entity_id="man-storage-key",
        engine_type="claude_code",
    )
    resolved = _run(
        m.resolve(ctx, [{"path": "data/a.md", "source": "https://x/a.bin"}])
    )
    assert resolved.ok, [f.reason for f in resolved.failures]
    _run(m.plan(ctx, resolved.intents))
    assert svc.exists_probes == [
        {
            "entity_type": "staff",
            "entity_id": "u_owner",
            "bot_id": "b_1",
            "engine_type": "claude_code",
            "path": "data/a.md",
        }
    ]


def test_declared_trees_report_as_removals_not_member_rows():
    """A declared tree's replacement rides the plan's removals channel.

    The old encoding put the tree in ``entries`` as a ``value=None``
    sentence-turn — so the dry-run projection emitted a report row for it
    while the real write skipped it (two shapes for one document), and an
    empty archive's destructive delete left no audit row at all. The
    removals channel is the engine's own answer for "an overwrite removes
    something with no declared entry to attach to": the tree reports under
    the category's ``removed``, the member files classify as entries.
    """
    archive = _tgz({"a.txt": b"AAA"})
    svc = FakeResourceFileService(exists_paths={"established/a.txt"})
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {
                    "path": "established/",
                    "unpack": "tar.gz",
                    "source": "https://x/old.tgz",
                },
                {
                    "path": "fresh/",
                    "unpack": "tar.gz",
                    "source": "https://x/new.tgz",
                },
            ],
        )
    )
    assert resolved.ok, [f.reason for f in resolved.failures]
    plan = _run(m.plan(ctx, resolved.intents))
    by_id = {p.intent.identity: p.outcome for p in plan.entries}
    # Members only: presence is probed per member file; a fresh upload under
    # a replaced tree classifies as created, exactly as a first apply would.
    assert by_id == {
        "established/a.txt": "updated",
        "fresh/a.txt": "created",
    }
    # Presence probes never ask about the tree roots — they have no label.
    assert all(not p["path"].endswith("/") for p in svc.exists_probes)
    # The declared trees, in declaration order, as removals.
    assert plan.removals == ("established/", "fresh/")
    assert not plan.is_noop


# --- write: delivery through the one write chain (Task 5) ---


def _write_through(m, ctx, entries):
    resolved = _run(m.resolve(ctx, entries))
    assert resolved.ok, [f.reason for f in resolved.failures]
    plan = _run(m.plan(ctx, resolved.intents))
    return plan, _run(m.write(ctx, plan))


def test_write_replaces_the_tree_then_uploads_every_member():
    svc = FakeResourceFileService(exists_paths={"wrap/old.txt"})
    archive = _tgz({"a.txt": b"AAA", "sub/b.txt": b"BBB"})
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="claude_code")
    plan, results = _write_through(
        m,
        ctx,
        [{"path": "wrap/", "unpack": "tar.gz", "source": "https://x/t.tgz"}],
    )
    # One delete per declared tree, first: the replace removes everything
    # under "wrap/" — including files the new archive no longer ships and
    # hand-added ones (the ownership rule).
    assert svc.deleted == ["wrap"]
    assert svc.writes == {
        ("wrap", "a.txt"): b"AAA",
        ("wrap/sub", "b.txt"): b"BBB",
    }
    # The tree replacement is an ownership action, not an entry: only its
    # member files report.
    assert [r.identity for r in results] == ["wrap/a.txt", "wrap/sub/b.txt"]
    assert all(r.outcome.value == "created" for r in results)
    # The tree is in the plan's removals — the audit row for the replace.
    assert plan.removals == ("wrap/",)


def test_write_deletes_the_declared_tree_without_the_trailing_slash():
    """F1: the write chain's file/dir branching keys on the path's shape.

    ``ResourceFileService._is_dir`` rpartitions ``path`` and looks the leaf
    up in the parent listing — for "wrap/" that leaf is the empty string and
    never matches, so the delete is routed to the single-file remove API
    ("not recursive" by that service's own docstring) and every transport
    under it swallows the refusal as ``False``. The materialiser must
    therefore hand the write chain the tree path *minus* the declaring
    slash, which routes to the recursive branch.
    """
    svc = FakeResourceFileService(exists_paths={"wrap/old.txt"})
    archive = _tgz({"a.txt": b"AAA"})
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="claude_code")
    _write_through(
        m, ctx, [{"path": "wrap/", "unpack": "tar.gz", "source": "https://x/t"}]
    )
    assert [c["path"] for c in svc.delete_calls] == ["wrap"]


def test_a_silently_failed_tree_delete_fails_its_members():
    """F1's second half: the transports refuse by returning ``False``, not
    by raising — all three device filesystems catch their own errors — so a
    write stage that only caught exceptions would see a refused rmtree as
    success and upload the new members over a tree that was never
    replaced, reporting ``created``. A ``False`` delete with the tree still
    present (re-probed) must fail the tree's members instead.
    """
    svc = FakeResourceFileService(
        exists_paths={"tools"}, fail_deletes={"tools"}
    )
    archive = _tgz({"a.md": b"A"})
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="claude_code")
    plan, results = _write_through(
        m,
        ctx,
        [
            {"path": "tools/", "unpack": "tar.gz", "source": "https://x/t.tgz"},
            {"path": "notes/r.md", "content": "# rules"},
        ],
    )
    by_id = {r.identity: r for r in results}
    assert by_id["tools/a.md"].outcome.value == "failed"
    assert by_id["tools/a.md"].reason == "directory tree replacement failed"
    # Nothing from the failed tree was uploaded; the sibling outside it
    # still converged.
    assert ("tools", "a.md") not in svc.writes
    assert by_id["notes/r.md"].outcome.value == "created"
    assert plan.removals == ("tools/",)


def test_a_failed_delete_of_an_absent_tree_is_not_a_failure():
    """``delete`` returns ``False`` when nothing was deleted — the real
    service's own contract — which is the *normal* answer for a first
    apply onto a tree that does not exist yet. The failure detection must
    re-probe presence, not read ``False`` as refusal.
    """
    svc = FakeResourceFileService(fail_deletes={"tools"})
    archive = _tgz({"a.md": b"A"})
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="claude_code")
    plan, results = _write_through(
        m,
        ctx,
        [{"path": "tools/", "unpack": "tar.gz", "source": "https://x/t.tgz"}],
    )
    assert [r.outcome.value for r in results] == ["created"]
    assert svc.writes == {("tools", "a.md"): b"A"}


def test_write_addresses_the_bot_owner():
    """The write chain gets the owner's address, the router's own way."""
    svc = FakeResourceFileService()
    archive = _tgz({"a.txt": b"AAA"})
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
    ctx = make_context(
        bot_id="b_1",
        owner_id="u_owner",
        entity_id="man-storage-key",
        engine_type="claude_code",
    )
    _write_through(
        m, ctx, [{"path": "wrap/", "unpack": "tar.gz", "source": "https://x/t"}]
    )
    assert svc.delete_calls == [
        {
            "entity_type": "staff",
            "entity_id": "u_owner",
            "bot_id": "b_1",
            "engine_type": "claude_code",
            "path": "wrap",
        }
    ]
    assert svc.upload_calls == [
        {
            "entity_type": "staff",
            "entity_id": "u_owner",
            "bot_id": "b_1",
            "engine_type": "claude_code",
            "target_dir": "wrap",
            "filename": "a.txt",
        }
    ]


def test_write_is_player_setup_convergent():
    """Applying N times equals applying once: same writes, same deletes, no growth.

    Both applies run against the SAME service state: the second pass is the
    convergence claim (the tree from apply #1 is deleted and rewritten, no
    duplicate rows, no stale entries), not a re-observation of apply #1.
    """
    archive = _tgz({"a.txt": b"AAA"})
    svc = FakeResourceFileService()
    for _ in range(2):
        m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
        ctx = make_context(engine_type="claude_code")
        _write_through(
            m,
            ctx,
            [{"path": "wrap/", "unpack": "tar.gz", "source": "https://x/t.tgz"}],
        )
    assert svc.writes == {("wrap", "a.txt"): b"AAA"}
    assert svc.deleted == ["wrap", "wrap"]


def test_write_failure_yields_failed_entry_per_member():
    class _Buggy(FakeResourceFileService):
        async def upload_file(self, **kw):
            if kw["filename"] == "b.txt":
                raise RuntimeError("transport down")
            return await super().upload_file(**kw)

    svc = _Buggy(exists_paths=set())
    archive = _tgz({"a.txt": b"A", "b.txt": "B".encode()})
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="claude_code")
    plan, results = _write_through(
        m,
        ctx,
        [{"path": "wrap/", "unpack": "tar.gz", "source": "https://x/t.tgz"}],
    )
    outcomes = {(r.identity, r.outcome.value) for r in results}
    assert ("wrap/b.txt", "failed") in outcomes
    assert ("wrap/a.txt", "failed") not in outcomes
    # The report row states the failure in its own words, never raw
    # exception text that might carry a credential.
    assert all(
        "transport down" not in (r.reason or "")
        for r in results
        if r.outcome.value == "failed"
    )
    assert svc.writes == {("wrap", "a.txt"): b"A"}


# --- registration: the fifth materialiser, and the artifact contract (Task 6) ---


def test_module_never_imports_the_artifact_contract():
    """W6 acceptance: ``BotConfigArtifact`` stays untouched.

    The whole module is AST-walked (not just top-level imports) so a lazy
    import inside a function cannot slip the contract in either.
    """
    import ast
    from pathlib import Path

    import agentclaw.community.core.bot_config_manifest.apply.materialisers.resources as _res

    tree = ast.parse(Path(_res.__file__).read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    for name in names:
        assert "kernel.bot_config" not in name, (
            "the resources materialiser must not reach the artifact contract"
        )


def test_build_materialisers_registers_five():
    from agentclaw.community.core.bot_config_manifest.apply.registry import (
        build_materialisers,
    )
    from agentclaw.community.core.bot_config_manifest.capabilities import (
        ManifestCategory,
    )

    registry = build_materialisers(
        script_service=object(),
        activation_service=object(),
        mcp_auth_service=object(),
        identity_service=object(),
        upload_service=object(),
        capability_reader=object(),
        package_validator=object(),
        entry_fetcher=object(),
        resource_service=object(),
    )
    assert ManifestCategory.RESOURCES in registry
    assert len(registry) == 5


# --- fetch categories and the keep_last contract (review findings) ---


def test_each_form_fetches_under_its_own_category():
    """Files fetch as ``resources_file``, archives as ``resources_archive``.

    Schema §5 states the two widths separately (100MB vs 200MB); a shared
    "resources" would leave the fetch funnel's cap lookup to its fallback,
    silently halving what the archive form allows — and the W11 linkage
    column would carry a category the vocabulary never defined.
    """
    archive = _tgz({"a.txt": b"x"})
    svc = FakeResourceFileService()
    stub = _StubEntryFetcher(archive)
    m = ResourcesMaterialiser(svc, stub)
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {"path": "data/a.md", "source": "https://x/a.bin"},
                {
                    "path": "tools/",
                    "unpack": "tar.gz",
                    "source": "https://x/t.tgz",
                },
            ],
        )
    )
    assert resolved.ok, [f.reason for f in resolved.failures]
    assert [c["category"] for c in stub.calls] == [
        "resources_file",
        "resources_archive",
    ]
    assert [c["entry_identity"] for c in stub.calls] == ["data/a.md", "tools/"]


def test_keep_last_fallback_surfaces_as_the_entries_note():
    """§9.6: a keep_last row states the fallback.

    The fetch pipeline hands the reason over as
    ``FetchedEntry.fallback_reason``; the materialiser must carry it into the
    intent's note — identity and skills already do, and a silent fallback is
    the contract broken quietly.
    """

    class _FallingBack(_StubEntryFetcher):
        def fetch(self, ctx, **kwargs):
            entry = super().fetch(ctx, **kwargs)
            return FetchedEntry(
                content=entry.content,
                digest=entry.digest,
                from_store=True,
                content_type=entry.content_type,
                fallback_reason=(
                    "delivered from the platform's stored copy (keep_last)"
                ),
            )

    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _FallingBack())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(ctx, [{"path": "data/a.md", "source": "https://x/a.bin"}])
    )
    assert resolved.ok, [f.reason for f in resolved.failures]
    assert (
        resolved.intents[0].note
        == "delivered from the platform's stored copy (keep_last)"
    )


def test_write_carries_the_note_onto_the_report_row():
    """The note reaches the report row — stating a keep_last fallback only in
    the log is §9.6 broken quietly."""

    class _FallingBack(_StubEntryFetcher):
        def fetch(self, ctx, **kwargs):
            entry = super().fetch(ctx, **kwargs)
            return FetchedEntry(
                content=entry.content,
                digest=entry.digest,
                from_store=True,
                content_type=entry.content_type,
                fallback_reason="fell back (keep_last)",
            )

    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _FallingBack())
    ctx = make_context(engine_type="claude_code")
    plan, results = _write_through(
        m, ctx, [{"path": "data/a.md", "source": "https://x/a.bin"}]
    )
    assert [r.note for r in results] == ["fell back (keep_last)"]


# --- the write chain's admission rules, asked before the first delete (H1) ---


def test_an_archive_member_the_chain_would_refuse_fails_at_resolve():
    """``upload_file`` refuses extensions outside its allow-list (no ``.sh``,
    no extensionless files). That refusal must land in ``resolve`` — before
    the tree marker promises the declared tree — or the category reaches
    write with a member that deterministically fails *after* the
    destructive delete, on every re-apply. The failure is keyed to the
    *declared* entry's identity with the member named in the reason, so
    the orchestrator's abort mapping can blame the declared row.
    """
    archive = _tgz({"a.md": b"ok", "run.sh": b"#!/bin/sh\n"})
    svc = FakeResourceFileService(exists_paths={"tools/old.md"})
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [{"path": "tools/", "unpack": "tar.gz", "source": "https://x/t.tgz"}],
        )
    )
    # The whole category aborts (§3.2): one undeliverable member means the
    # tree is NOT deleted for a partial delivery.
    assert not resolved.ok
    assert resolved.failures[0].identity == "tools/"
    # The reason names the offending member inside the declared entry.
    assert "tools/run.sh" in resolved.failures[0].reason
    assert "file type" in resolved.failures[0].reason.lower()


def test_an_extensionless_inline_file_fails_at_resolve():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "data/LICENSE", "content": "MIT"}]))
    assert not resolved.ok
    assert "file type" in resolved.failures[0].reason.lower()


def test_inline_content_over_the_size_cap_fails_at_resolve(
    monkeypatch,
):
    """Inline ``content`` never goes through the fetch funnel's caps, so the
    delivery gate is the only line a 600MB inline entry meets — and it must
    meet it in ``resolve``, not as a write-stage surprise."""
    from agentclaw.community.core.resources.services import file_service as fs

    monkeypatch.setattr(fs, "MAX_FILE_SIZE", 16)
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(ctx, [{"path": "data/big.md", "content": "x" * 32}])
    )
    assert not resolved.ok
    # The write chain's own words — the gate delegates to the same
    # predicate ``upload_file`` enforces, so the refusal texts agree.
    assert "too large" in resolved.failures[0].reason.lower()


def test_strip_components_of_the_wrong_type_is_a_resolve_failure():
    """A document from before the schema's type check: no raw ``TypeError``
    may escape ``resolve`` — the orchestrator would abort the category with
    the exception's text in it."""
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(_tgz({"a.md": b"x"})))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {
                    "path": "tools/",
                    "unpack": "tar.gz",
                    "strip_components": "one",
                    "source": "https://x/t.tgz",
                }
            ],
        )
    )
    assert not resolved.ok
    assert "strip_components" in resolved.failures[0].reason


def test_a_failed_tree_replacement_fails_its_members_in_composed_words():
    """M2: the sentinel delete is part of delivery — a raise there must not
    escape as raw exception text (the orchestrator would abort the whole
    category quoting it); the tree's members answer FAILED, in the stage's
    own words, and other categories' entries in this one keep their shot.
    """

    class _BuggyTreeDelete(FakeResourceFileService):
        async def delete(self, **kwargs):
            if kwargs["path"] == "tools":
                raise RuntimeError("rmtree quoted a header with a token")
            return await super().delete(**kwargs)

    archive = _tgz({"a.md": b"A"})
    svc = _BuggyTreeDelete(exists_paths=set())
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="claude_code")
    plan, results = _write_through(
        m,
        ctx,
        [
            {
                "path": "tools/",
                "unpack": "tar.gz",
                "source": "https://x/t.tgz",
            },
            {"path": "notes/r.md", "content": "# rules"},
        ],
    )
    by_id = {r.identity: r for r in results}
    assert by_id["tools/a.md"].outcome.value == "failed"
    assert by_id["tools/a.md"].reason == "directory tree replacement failed"
    # the raw exception text never reached the report
    assert all("token" not in (r.reason or "") for r in results)
    # the sibling entry outside the failed tree was still delivered
    assert by_id["notes/r.md"].outcome.value == "created"


# --- report shape: the tree channel, end to end (review F4) ---


def _resources_engine(svc, fetcher):
    """An orchestrator over the real registry, resources wired to the rig."""
    return ApplyOrchestrator(
        build_materialisers(
            script_service=FakeStartupScriptService(),
            activation_service=FakeActivationService(),
            mcp_auth_service=FakeMcpAuth(),
            identity_service=FakeIdentityService(),
            upload_service=FakeSkillUploadService(),
            capability_reader=FakeCapabilityReader(),
            package_validator=real_validator(),
            entry_fetcher=fetcher,
            resource_service=svc,
        ),
        steps=steps_for
    )


_DOCUMENT = {
    "schema_version": 1,
    "manifest": {
        "resources": [
            {
                "path": "wrap/",
                "unpack": "tar.gz",
                "source": "https://x/t.tgz",
            },
            {"path": "notes/r.md", "content": "# rules"},
        ]
    },
}


def test_dry_run_and_apply_report_the_same_resources_shape():
    """F4: preview and execution must answer in one shape.

    The old encoding put the declared tree in ``entries`` (the dry-run
    projection emits one row per planned entry) while the real write
    skipped it — one document, two report shapes, and any caller diffing
    preview against execution saw a row vanish. With the tree in
    ``removals`` both paths take the same plan and report the same rows
    plus the same ``removed``.
    """
    archive = _tgz({"a.txt": b"AAA"})
    svc = FakeResourceFileService()
    engine = _resources_engine(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="claude_code")

    dry = _run(
        engine.apply(
            ctx, _DOCUMENT, apply_id="a1", trigger="explicit",
            started_at=datetime.now(), dry_run=True,
        )
    )
    real = _run(
        engine.apply(
            ctx, _DOCUMENT, apply_id="a2", trigger="explicit",
            started_at=datetime.now(), dry_run=False,
        )
    )

    for report in (dry, real):
        resources = next(
            c for c in report.categories if c.construct.value == "resources"
        )
        assert resources.removals == ("wrap/",)
    dry_rows = {e.identity: e.outcome.value for e in dry.entries}
    real_rows = {e.identity: e.outcome.value for e in real.entries}
    assert dry_rows == {"notes/r.md": "created", "wrap/a.txt": "created"}
    assert real_rows == {"notes/r.md": "created", "wrap/a.txt": "created"}
    # The preview wrote nothing; the execution delivered.
    assert svc.writes == {("notes", "r.md"): b"# rules", ("wrap", "a.txt"): b"AAA"}
    assert svc.deleted == ["wrap"]


def test_an_empty_archive_still_audits_the_tree_removal():
    """F4's tail case: a dirs-only (or empty) archive replaces the declared
    tree with nothing. The old encoding deleted the whole tree while
    emitting zero report rows — a destructive operation with no audit
    trail. The removals channel makes the replace visible as ``removed``
    in both shapes.
    """
    archive = _tgz({})
    svc = FakeResourceFileService(exists_paths={"gone/old.md"})
    engine = _resources_engine(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="claude_code")

    dry = _run(
        engine.apply(
            ctx,
            {
                "schema_version": 1,
                "manifest": {
                    "resources": [
                        {
                            "path": "gone/",
                            "unpack": "tar.gz",
                            "source": "https://x/empty.tgz",
                        }
                    ]
                },
            },
            apply_id="a1", trigger="explicit",
            started_at=datetime.now(), dry_run=True,
        )
    )
    real = _run(
        engine.apply(
            ctx,
            {
                "schema_version": 1,
                "manifest": {
                    "resources": [
                        {
                            "path": "gone/",
                            "unpack": "tar.gz",
                            "source": "https://x/empty.tgz",
                        }
                    ]
                },
            },
            apply_id="a2", trigger="explicit",
            started_at=datetime.now(), dry_run=False,
        )
    )

    for report in (dry, real):
        resources = next(
            c for c in report.categories if c.construct.value == "resources"
        )
        assert resources.removals == ("gone/",)
        assert resources.as_dict()["removed"] == ["gone/"]
    assert [e.identity for e in real.entries] == []
    # The tree was deleted for real, and nothing was delivered.
    assert svc.deleted == ["gone"]
    assert svc.writes == {}


def test_a_refused_member_blames_the_declared_entry_in_the_report():
    """F3: the report must say which member was undeliverable and why.

    The orchestrator's abort mapping keys failure reasons onto *declared*
    entry identities — a member-keyed failure matches nothing and both
    declared rows answer a generic "another entry could not be
    materialized", leaving the author to guess which of the archive's
    members violated the write chain's admission and in what way.
    """
    archive = _tgz({"a.md": b"ok", "run.sh": b"#!/bin/sh\n"})
    svc = FakeResourceFileService(exists_paths={"tools/old.md"})
    engine = _resources_engine(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="claude_code")

    report = _run(
        engine.apply(
            ctx,
            {
                "schema_version": 1,
                "manifest": {
                    "resources": [
                        {
                            "path": "tools/",
                            "unpack": "tar.gz",
                            "source": "https://x/t.tgz",
                        },
                        {"path": "notes/r.md", "content": "# rules"},
                    ]
                },
            },
            apply_id="a1", trigger="explicit",
            started_at=datetime.now(), dry_run=False,
        )
    )

    rows = {(e.identity, e.outcome.value): e for e in report.entries}
    (identity, outcome), row = next(
        item for item in rows.items() if item[0][0] == "tools/"
    )
    assert outcome == "failed"
    assert "run.sh" in (row.reason or "")
    assert "file type" in (row.reason or "").lower()
    # The blameless neighbour keeps the generic skip wording.
    assert rows[("notes/r.md", "skipped")].reason is not None
    # Nothing was written and the tree still stands.
    assert svc.writes == {}
    assert svc.deleted == []


# --- the write address's engine half: the runtime routing (F2) ---


def test_routed_bots_address_the_runtime_engine_workspace():
    """F2: the workspace address routes the engine the router's way.

    The resources router resolves the engine through the runtime routing
    policy (``claude_code`` + a non-``normalCC`` template ⇒ ``aicoding``)
    before composing ``{bot_dir}/{engine}/workspace``; a raw
    ``active_engine`` would deliver the files into a tree neither the
    resources console nor the bot reads while the report says SUCCEEDED.
    """
    archive = _tgz({"a.txt": b"A"})
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
    ctx = make_context(
        engine_type="claude_code", bot={"template_type": "applicationCoding"}
    )
    _write_through(
        m, ctx, [{"path": "wrap/", "unpack": "tar.gz", "source": "https://x/t"}]
    )
    assert svc.delete_calls[0]["engine_type"] == "aicoding"
    assert all(c["engine_type"] == "aicoding" for c in svc.upload_calls)
    assert all(p["engine_type"] == "aicoding" for p in svc.exists_probes)


def test_unrouted_bots_keep_the_active_engine():
    """The control cases: no template, the ``normalCC`` template, and a
    plain non-claude_code engine all keep the address on the active engine
    — the routing policy must be the only thing that moves the address."""
    archive = _tgz({"a.txt": b"A"})
    cases = [
        {},  # fake default: claude_code, no template_type
        {"template_type": "normalCC"},
    ]
    for overlay in cases:
        svc = FakeResourceFileService()
        m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
        ctx = make_context(engine_type="claude_code", bot=overlay)
        _write_through(
            m, ctx, [{"path": "wrap/", "unpack": "tar.gz", "source": "https://x/t"}]
        )
        assert svc.delete_calls[0]["engine_type"] == "claude_code"

    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="openclaw")
    _write_through(
        m, ctx, [{"path": "wrap/", "unpack": "tar.gz", "source": "https://x/t"}]
    )
    assert svc.delete_calls[0]["engine_type"] == "openclaw"


def test_an_engineless_bot_record_falls_back_to_the_context_engine():
    """A record without a readable engine (``active_engine`` None) falls
    back to ``ctx.engine_type`` — never an empty string, which the path
    factory would compose into the bot root instead of an engine dir."""
    archive = _tgz({"a.txt": b"A"})
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(archive))
    ctx = make_context(engine_type="openclaw", bot={"active_engine": None})
    _write_through(
        m, ctx, [{"path": "wrap/", "unpack": "tar.gz", "source": "https://x/t"}]
    )
    assert svc.delete_calls[0]["engine_type"] == "openclaw"


# --- the apply-time path belt re-asks the schema's own rule (F6) ---


def test_a_tilde_path_is_refused_at_resolve():
    """A document that predates the validator carries the PUT layer's
    refusals into apply: "~" is an absolute-ish path in exactly the way
    "/" is, and the old belt (only "/", ".." segments) let it through to
    the write chain."""
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "~/x.md", "content": "x"}]))
    assert not resolved.ok
    assert resolved.failures[0].identity == "~/x.md"
    assert "~" in resolved.failures[0].reason


def test_a_windows_drive_path_is_refused_at_resolve():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "C:evil.md", "content": "x"}]))
    assert not resolved.ok
    assert "workspace-relative" in resolved.failures[0].reason


def test_duplicate_declared_paths_abort_the_category():
    """The schema refuses a duplicate resource path at PUT; the belt must
    re-ask it, or two entries at one path produce two intents with one
    identity, two report rows, and a last-write-wins order no rule defines."""
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {"path": "data/a.md", "content": "first"},
                {"path": "data/a.md", "source": "https://x/a"},
            ],
        )
    )
    assert not resolved.ok
    # The mcp/siblings' "seen" refusal convention: the duplicate is blamed
    # and the category aborts (§3.2) — the intents resolved before the
    # refusal are never consumed, the orchestrator aborts on ``not ok``
    # without looking at them. Nothing writable survives the apply.
    assert any("more than once" in f.reason for f in resolved.failures)


def test_an_unknown_unpack_kind_is_refused():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(_tgz({"a.md": b"x"})))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {
                    "path": "wrap/",
                    "unpack": "tgz",
                    "source": "https://x/t.tgz",
                }
            ],
        )
    )
    assert not resolved.ok
    assert "zip|tar.gz" in resolved.failures[0].reason


def test_refusals_quote_the_write_chains_own_words():
    """The resolve gate's verdict is the write chain's own text`` —`

    delegated to the one predicate (``file_service.admission_refusal``)
    rather than restated, the two surfaces cannot drift into disagreeing
    about what they admit.
    """
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "data/run.sh", "content": "#"}]))
    assert not resolved.ok
    assert resolved.failures[0].reason.startswith("File type not allowed")


# --- the port the wiring keys on (F7) ---


def test_the_port_mirrors_the_apply_side_call_surface():
    """The port is apply's call surface, exactly — and a keyword subset of
    the real service.

    A ``@runtime_checkable`` isinstance checks method *presence* only, so
    nothing else notices a signature drift; pinned here by reflection:
    every parameter the port declares must exist on the real
    ``ResourceFileService`` method with the same declared default (the
    fake mirrors the port in turn). The port is deliberately *narrower*
    than the real service — ``preserve_structure`` and ``publish_id`` /
    ``device_uuid`` are the router's vocabulary, not apply's.
    """
    import inspect

    from agentclaw.community.core.bot_config_manifest.apply.resource_port import (
        ManifestResourcePort,
    )
    from agentclaw.community.core.services.resource_file_service import (
        ResourceFileService,
    )

    def _kwparams(func) -> dict[str, Any]:
        return {
            name: param.default
            for name, param in inspect.signature(func).parameters.items()
            if param.kind == inspect.Parameter.KEYWORD_ONLY
        }

    for method in ("upload_file", "delete", "exists"):
        port_params = _kwparams(getattr(ManifestResourcePort, method))
        real_params = _kwparams(getattr(ResourceFileService, method))
        # Every port parameter exists on the real service...
        assert set(port_params) <= set(real_params), (method, port_params)
        # ...with the same declared default where the port declares one.
        for name, default in port_params.items():
            assert real_params[name] == default, (method, name)
        # ...and every parameter the port declares is one apply passes.
        assert "preserve_structure" not in port_params, method
        # The one divergence the apply side must not reintroduce: ``exists``
        # is addressed like ``delete`` and ``upload_file``, entity and all.
        assert "entity_type" in _kwparams(ManifestResourcePort.exists)


def test_an_unhashable_unpack_kind_is_refused_not_raised():
    """The membership check is against a frozenset (``VALID_UNPACK``); an
    unhashable ``unpack`` (a YAML list, from a validator-bypassing document)
    must still get the belt's clean refusal — a raw ``TypeError`` would
    leave the category's failure text to the orchestrator's exception
    path instead of the stage's own words.
    """
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher(_tgz({"a.md": b"x"})))
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {
                    "path": "wrap/",
                    "unpack": ["zip"],
                    "source": "https://x/t.tgz",
                }
            ],
        )
    )
    assert not resolved.ok
    assert "zip|tar.gz" in resolved.failures[0].reason
