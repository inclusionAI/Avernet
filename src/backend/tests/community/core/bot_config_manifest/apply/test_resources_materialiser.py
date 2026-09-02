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
from typing import Any

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetchError,
    FetchedEntry,
)
from agentclaw.community.core.bot_config_manifest.apply.materialisers.resources import (
    ResourcesMaterialiser,
)

from ._fakes import FakeResourceFileService, build_skill_tgz, make_context


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
    # The "wrap/" entry is the directory sentinel (value=None, the
    # tree-replacement marker), riding first so write deletes the tree
    # before any member uploads.
    assert got == {
        "wrap/": None,
        "wrap/a.txt": b"AAA",
        "wrap/sub/b.txt": b"BBB",
    }
    assert resolved.intents[0].identity == "wrap/"


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


def test_plan_classifies_the_directory_sentinel_within_the_report_vocabulary():
    """The sentinel classifies like any entry — the vocabulary stays the enum's.

    A bespoke outcome ("replaced") would crash the orchestrator's dry-run
    projection, which feeds every planned outcome through
    ``EntryOutcome(...)``; write recognises the sentinel by its ``value is
    None``, not by the label.
    """
    archive = _tgz({"a.txt": b"AAA"})
    svc = FakeResourceFileService(exists_paths={"established/"})
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
    # Presence is probed per identity: only the "established/" tree root was
    # seeded present, so its member — a fresh upload under a replaced tree —
    # classifies as created, exactly as a first apply of the member would.
    assert by_id == {
        "established/": "updated",
        "established/a.txt": "created",
        "fresh/": "created",
        "fresh/a.txt": "created",
    }


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
    assert svc.deleted == ["wrap/"]
    assert svc.writes == {
        ("wrap", "a.txt"): b"AAA",
        ("wrap/sub", "b.txt"): b"BBB",
    }
    # The sentinel is an ownership action, not an entry: only its member
    # files report.
    assert [r.identity for r in results] == ["wrap/a.txt", "wrap/sub/b.txt"]
    assert all(r.outcome.value == "created" for r in results)


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
            "path": "wrap/",
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
    assert svc.deleted == ["wrap/", "wrap/"]


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
    the sentinel deletes the declared tree — or the category reaches write
    with a member that deterministically fails *after* the destructive
    delete, on every re-apply.
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
    assert resolved.failures[0].identity == "tools/run.sh"
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
    assert "size cap" in resolved.failures[0].reason.lower()


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
            if kwargs["path"] == "tools/":
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
